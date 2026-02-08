"""
Clearledgr Core Database

Unified database layer that all surfaces connect to.
Supports PostgreSQL (production) and SQLite (development).
"""

import os
import json
import sqlite3
from typing import List, Optional, Dict, Any, Tuple
from contextlib import contextmanager
from datetime import datetime, timezone

try:
    import psycopg
    HAS_POSTGRES = True
except ImportError:
    psycopg = None
    HAS_POSTGRES = False

from clearledgr.core.models import (
    Transaction, Match, Exception, DraftEntry, FinanceEmail, AuditLog,
    TransactionSource, TransactionStatus, ExceptionType, ExceptionPriority, ApprovalStatus
)


class ClearledgrDB:
    """
    Central database for all Clearledgr data.
    
    This is the SINGLE SOURCE OF TRUTH.
    All surfaces (Gmail, Sheets, Slack) read and write through this.
    """
    
    def __init__(self, db_path: str = "clearledgr.db"):
        self.dsn = os.getenv("DATABASE_URL")
        self.db_path = db_path
        self.use_postgres = bool(self.dsn and HAS_POSTGRES)
        self._initialized = False
    
    def initialize(self):
        """Create all tables if they don't exist."""
        if self._initialized:
            return
        
        with self.connect() as conn:
            cur = conn.cursor()
            
            # Transactions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    amount REAL NOT NULL,
                    currency TEXT DEFAULT 'EUR',
                    date TEXT,
                    description TEXT,
                    reference TEXT,
                    source TEXT NOT NULL,
                    source_id TEXT,
                    vendor TEXT,
                    status TEXT DEFAULT 'pending',
                    matched_with TEXT,
                    match_confidence REAL DEFAULT 0,
                    match_score INTEGER DEFAULT 0,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metadata TEXT
                )
            """)
            
            # Matches table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
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
                    organization_id TEXT,
                    created_at TEXT
                )
            """)
            
            # Exceptions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS exceptions (
                    id TEXT PRIMARY KEY,
                    transaction_id TEXT NOT NULL,
                    transaction_source TEXT,
                    type TEXT NOT NULL,
                    priority TEXT DEFAULT 'medium',
                    amount REAL,
                    currency TEXT DEFAULT 'EUR',
                    vendor TEXT,
                    near_matches TEXT,
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
                    organization_id TEXT,
                    created_at TEXT
                )
            """)
            
            # Draft entries table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS draft_entries (
                    id TEXT PRIMARY KEY,
                    match_id TEXT,
                    debit_account TEXT,
                    credit_account TEXT,
                    amount REAL,
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
                    organization_id TEXT,
                    created_at TEXT
                )
            """)
            
            # Finance emails table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS finance_emails (
                    id TEXT PRIMARY KEY,
                    gmail_id TEXT UNIQUE,
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
                    organization_id TEXT,
                    user_id TEXT,
                    created_at TEXT
                )
            """)
            
            # Audit log table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    user_id TEXT,
                    user_email TEXT,
                    surface TEXT,
                    changes TEXT,
                    metadata TEXT,
                    organization_id TEXT,
                    timestamp TEXT
                )
            """)
            
            # OAuth tokens table (Gmail, etc.)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT,
                    provider TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    token_type TEXT DEFAULT 'Bearer',
                    expires_at TEXT,
                    scopes TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(user_id, provider)
                )
            """)

            # Gmail autonomous scanning state
            cur.execute("""
                CREATE TABLE IF NOT EXISTS gmail_autopilot_state (
                    user_id TEXT PRIMARY KEY,
                    email TEXT,
                    last_scan_at TEXT,
                    last_history_id TEXT,
                    watch_expiration TEXT,
                    last_watch_at TEXT,
                    last_error TEXT,
                    updated_at TEXT
                )
            """)
            
            # ERP connections table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS erp_connections (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    erp_type TEXT NOT NULL,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expiry TEXT,
                    realm_id TEXT,
                    tenant_id TEXT,
                    base_url TEXT,
                    credentials TEXT,
                    is_active INTEGER DEFAULT 1,
                    last_sync_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(organization_id, erp_type)
                )
            """)
            
            # Users table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT,
                    password_hash TEXT,
                    role TEXT DEFAULT 'member',
                    organization_id TEXT,
                    is_active INTEGER DEFAULT 1,
                    last_login_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Organizations table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE,
                    settings TEXT,
                    features TEXT,
                    subscription_tier TEXT DEFAULT 'free',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Slack threads table - tracks Slack conversations per invoice
            cur.execute("""
                CREATE TABLE IF NOT EXISTS slack_threads (
                    id TEXT PRIMARY KEY,
                    invoice_id TEXT NOT NULL,
                    gmail_id TEXT,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    message_ts TEXT,
                    status TEXT DEFAULT 'pending',
                    approved_by TEXT,
                    approved_at TEXT,
                    rejection_reason TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(invoice_id, channel_id)
                )
            """)
            
            # Invoice status tracking - explicit state machine
            cur.execute("""
                CREATE TABLE IF NOT EXISTS invoice_status (
                    id TEXT PRIMARY KEY,
                    gmail_id TEXT UNIQUE NOT NULL,
                    email_subject TEXT,
                    vendor TEXT,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    invoice_number TEXT,
                    due_date TEXT,
                    status TEXT DEFAULT 'new',
                    confidence REAL DEFAULT 0,
                    erp_vendor_id TEXT,
                    erp_bill_id TEXT,
                    slack_thread_id TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    posted_at TEXT,
                    rejection_reason TEXT,
                    organization_id TEXT,
                    user_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Create indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_org ON transactions(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_source ON transactions(source)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_org ON exceptions(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_status ON exceptions(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_finance_emails_org ON finance_emails(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_tokens(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_tokens(provider)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gmail_autopilot_email ON gmail_autopilot_state(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_erp_org ON erp_connections(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_slack_threads_invoice ON slack_threads(invoice_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_slack_threads_org ON slack_threads(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_invoice_status_gmail ON invoice_status(gmail_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_invoice_status_org ON invoice_status(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_invoice_status_status ON invoice_status(status)")
            
            # AP Invoices table (for AP workflow)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_invoices (
                    id TEXT PRIMARY KEY,
                    invoice_number TEXT,
                    vendor_name TEXT,
                    vendor_id TEXT,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    due_date TEXT,
                    gl_code TEXT,
                    description TEXT,
                    status TEXT DEFAULT 'pending',
                    email_id TEXT,
                    thread_id TEXT,
                    po_number TEXT,
                    confidence REAL DEFAULT 0,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # AP Payments table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ap_payments (
                    id TEXT PRIMARY KEY,
                    invoice_id TEXT,
                    vendor_id TEXT,
                    vendor_name TEXT,
                    amount REAL,
                    currency TEXT DEFAULT 'USD',
                    method TEXT DEFAULT 'ach',
                    status TEXT DEFAULT 'pending',
                    batch_id TEXT,
                    scheduled_date TEXT,
                    sent_at TEXT,
                    completed_at TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # GL Corrections table
            cur.execute("""
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
            """)
            
            # Recurring Rules table
            cur.execute("""
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
            """)
            
            # ERP Sync Tracking table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS erp_sync_tracking (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT UNIQUE,
                    email_id TEXT,
                    invoice_id TEXT,
                    erp_type TEXT,
                    erp_id TEXT,
                    erp_status TEXT,
                    synced INTEGER DEFAULT 0,
                    last_synced TEXT,
                    sync_error TEXT,
                    organization_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Create indexes for new tables
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_invoices_org ON ap_invoices(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_invoices_status ON ap_invoices(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_invoices_vendor ON ap_invoices(vendor_name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_payments_org ON ap_payments(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ap_payments_status ON ap_payments(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gl_corrections_org ON gl_corrections(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_gl_corrections_vendor ON gl_corrections(vendor)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_recurring_rules_org ON recurring_rules(organization_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_recurring_rules_vendor ON recurring_rules(vendor_pattern)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_erp_sync_thread ON erp_sync_tracking(thread_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_erp_sync_org ON erp_sync_tracking(organization_id)")
            
            conn.commit()
        
        self._initialized = True
    
    @contextmanager
    def connect(self):
        """Get database connection."""
        if self.use_postgres:
            conn = psycopg.connect(self.dsn)
            try:
                yield conn
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
    
    def _prepare_sql(self, sql: str) -> str:
        """Convert ? placeholders to %s for PostgreSQL."""
        if self.use_postgres:
            return sql.replace("?", "%s")
        return sql
    
    # ==================== TRANSACTIONS ====================
    
    def save_transaction(self, tx: Transaction) -> Transaction:
        """Save or update a transaction."""
        self.initialize()
        tx.updated_at = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO transactions 
            (id, amount, currency, date, description, reference, source, source_id, 
             vendor, status, matched_with, match_confidence, match_score, 
             organization_id, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                tx.id, tx.amount, tx.currency, tx.date, tx.description, tx.reference,
                tx.source.value, tx.source_id, tx.vendor, tx.status.value,
                json.dumps(tx.matched_with), tx.match_confidence, tx.match_score,
                tx.organization_id, tx.created_at, tx.updated_at, json.dumps(tx.metadata)
            ))
            conn.commit()
        
        self._audit("created" if not tx.updated_at else "updated", "transaction", tx.id, tx.organization_id)
        return tx
    
    def get_transaction(self, tx_id: str) -> Optional[Transaction]:
        """Get a transaction by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM transactions WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (tx_id,))
            row = cur.fetchone()
            
        if not row:
            return None
        return self._row_to_transaction(row)
    
    def get_transactions(
        self, 
        organization_id: str,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100
    ) -> List[Transaction]:
        """Get transactions with optional filters."""
        self.initialize()
        
        conditions = ["organization_id = ?"]
        params: List[Any] = [organization_id]
        
        if status:
            conditions.append("status = ?")
            params.append(status)
        if source:
            conditions.append("source = ?")
            params.append(source)
        
        sql = self._prepare_sql(f"""
            SELECT * FROM transactions 
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ?
        """)
        params.append(limit)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        
        return [self._row_to_transaction(row) for row in rows]
    
    def _row_to_transaction(self, row) -> Transaction:
        """Convert database row to Transaction object."""
        return Transaction(
            id=row['id'],
            amount=row['amount'],
            currency=row['currency'],
            date=row['date'],
            description=row['description'],
            reference=row['reference'],
            source=TransactionSource(row['source']),
            source_id=row['source_id'],
            vendor=row['vendor'],
            status=TransactionStatus(row['status']),
            matched_with=json.loads(row['matched_with'] or '[]'),
            match_confidence=row['match_confidence'],
            match_score=row['match_score'],
            organization_id=row['organization_id'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            metadata=json.loads(row['metadata'] or '{}'),
        )
    
    # ==================== MATCHES ====================
    
    def save_match(self, match: Match) -> Match:
        """Save a reconciliation match."""
        self.initialize()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO matches
            (id, gateway_id, bank_id, internal_id, score, confidence, match_type,
             amount_score, date_score, description_score, reference_score,
             is_three_way, is_approved, approved_by, approved_at, organization_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                match.id, match.gateway_id, match.bank_id, match.internal_id,
                match.score, match.confidence, match.match_type,
                match.amount_score, match.date_score, match.description_score, match.reference_score,
                1 if match.is_three_way else 0, 1 if match.is_approved else 0,
                match.approved_by, match.approved_at, match.organization_id, match.created_at
            ))
            conn.commit()
        
        self._audit("created", "match", match.id, match.organization_id)
        return match
    
    def get_matches(self, organization_id: str, limit: int = 100) -> List[Match]:
        """Get matches for an organization."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM matches WHERE organization_id = ? ORDER BY created_at DESC LIMIT ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()
        
        return [self._row_to_match(row) for row in rows]
    
    def _row_to_match(self, row) -> Match:
        return Match(
            id=row['id'],
            gateway_id=row['gateway_id'],
            bank_id=row['bank_id'],
            internal_id=row['internal_id'],
            score=row['score'],
            confidence=row['confidence'],
            match_type=row['match_type'],
            amount_score=row['amount_score'],
            date_score=row['date_score'],
            description_score=row['description_score'],
            reference_score=row['reference_score'],
            is_three_way=bool(row['is_three_way']),
            is_approved=bool(row['is_approved']),
            approved_by=row['approved_by'],
            approved_at=row['approved_at'],
            organization_id=row['organization_id'],
            created_at=row['created_at'],
        )
    
    # ==================== EXCEPTIONS ====================
    
    def save_exception(self, exc: Exception) -> Exception:
        """Save an exception."""
        self.initialize()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO exceptions
            (id, transaction_id, transaction_source, type, priority, amount, currency, vendor,
             near_matches, nearest_amount_diff, nearest_date_diff, ai_explanation, ai_suggested_action,
             status, resolved_by, resolved_at, resolution_notes, assigned_to, escalated_to,
             organization_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                exc.id, exc.transaction_id, exc.transaction_source.value, exc.type.value, exc.priority.value,
                exc.amount, exc.currency, exc.vendor, json.dumps(exc.near_matches),
                exc.nearest_amount_diff, exc.nearest_date_diff, exc.ai_explanation, exc.ai_suggested_action,
                exc.status, exc.resolved_by, exc.resolved_at, exc.resolution_notes,
                exc.assigned_to, exc.escalated_to, exc.organization_id, exc.created_at
            ))
            conn.commit()
        
        self._audit("created", "exception", exc.id, exc.organization_id)
        return exc
    
    def get_exceptions(
        self, 
        organization_id: str, 
        status: str = "open",
        limit: int = 100
    ) -> List[Exception]:
        """Get exceptions for an organization."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM exceptions WHERE organization_id = ? AND status = ? 
            ORDER BY 
                CASE priority 
                    WHEN 'critical' THEN 1 
                    WHEN 'high' THEN 2 
                    WHEN 'medium' THEN 3 
                    ELSE 4 
                END,
                created_at DESC
            LIMIT ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, status, limit))
            rows = cur.fetchall()
        
        return [self._row_to_exception(row) for row in rows]
    
    def _row_to_exception(self, row) -> Exception:
        return Exception(
            id=row['id'],
            transaction_id=row['transaction_id'],
            transaction_source=TransactionSource(row['transaction_source']),
            type=ExceptionType(row['type']),
            priority=ExceptionPriority(row['priority']),
            amount=row['amount'],
            currency=row['currency'],
            vendor=row['vendor'],
            near_matches=json.loads(row['near_matches'] or '[]'),
            nearest_amount_diff=row['nearest_amount_diff'],
            nearest_date_diff=row['nearest_date_diff'],
            ai_explanation=row['ai_explanation'],
            ai_suggested_action=row['ai_suggested_action'],
            status=row['status'],
            resolved_by=row['resolved_by'],
            resolved_at=row['resolved_at'],
            resolution_notes=row['resolution_notes'],
            assigned_to=row['assigned_to'],
            escalated_to=row['escalated_to'],
            organization_id=row['organization_id'],
            created_at=row['created_at'],
        )
    
    # ==================== FINANCE EMAILS ====================
    
    def save_finance_email(self, email: FinanceEmail) -> FinanceEmail:
        """Save a detected finance email."""
        self.initialize()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO finance_emails
            (id, gmail_id, subject, sender, received_at, email_type, confidence,
             vendor, amount, currency, invoice_number, status, processed_at,
             transaction_id, organization_id, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                email.id, email.gmail_id, email.subject, email.sender, email.received_at,
                email.email_type, email.confidence, email.vendor, email.amount, email.currency,
                email.invoice_number, email.status, email.processed_at, email.transaction_id,
                email.organization_id, email.user_id, email.created_at
            ))
            conn.commit()
        
        return email
    
    def get_finance_emails(
        self, 
        organization_id: str, 
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[FinanceEmail]:
        """Get finance emails for an organization."""
        self.initialize()
        
        if status:
            sql = self._prepare_sql("""
                SELECT * FROM finance_emails WHERE organization_id = ? AND status = ?
                ORDER BY received_at DESC LIMIT ?
            """)
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql("""
                SELECT * FROM finance_emails WHERE organization_id = ?
                ORDER BY received_at DESC LIMIT ?
            """)
            params = (organization_id, limit)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return [self._row_to_finance_email(row) for row in rows]

    def get_finance_email_by_gmail_id(self, gmail_id: str) -> Optional[FinanceEmail]:
        """Get a finance email by Gmail message ID."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM finance_emails WHERE gmail_id = ? LIMIT 1
        """)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gmail_id,))
            row = cur.fetchone()

        if not row:
            return None
        return self._row_to_finance_email(row)
    
    def _row_to_finance_email(self, row) -> FinanceEmail:
        return FinanceEmail(
            id=row['id'],
            gmail_id=row['gmail_id'],
            subject=row['subject'],
            sender=row['sender'],
            received_at=row['received_at'],
            email_type=row['email_type'],
            confidence=row['confidence'],
            vendor=row['vendor'],
            amount=row['amount'],
            currency=row['currency'],
            invoice_number=row['invoice_number'],
            status=row['status'],
            processed_at=row['processed_at'],
            transaction_id=row['transaction_id'],
            organization_id=row['organization_id'],
            user_id=row['user_id'],
            created_at=row['created_at'],
        )
    
    # ==================== DRAFT ENTRIES ====================
    
    def save_draft_entry(self, draft: DraftEntry) -> DraftEntry:
        """Save a draft journal entry."""
        self.initialize()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO draft_entries
            (id, match_id, debit_account, credit_account, amount, currency, description,
             posting_date, confidence, auto_generated, status, approved_by, approved_at,
             rejection_reason, posted_to_erp, erp_document_id, posted_at, organization_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                draft.id, draft.match_id, draft.debit_account, draft.credit_account,
                draft.amount, draft.currency, draft.description, draft.posting_date,
                draft.confidence, 1 if draft.auto_generated else 0, draft.status.value,
                draft.approved_by, draft.approved_at, draft.rejection_reason,
                1 if draft.posted_to_erp else 0, draft.erp_document_id, draft.posted_at,
                draft.organization_id, draft.created_at
            ))
            conn.commit()
        
        self._audit("created", "draft_entry", draft.id, draft.organization_id)
        return draft
    
    def get_draft_entries(
        self, 
        organization_id: str, 
        status: str = "pending",
        limit: int = 100
    ) -> List[DraftEntry]:
        """Get draft entries for an organization."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM draft_entries WHERE organization_id = ? AND status = ?
            ORDER BY created_at DESC LIMIT ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, status, limit))
            rows = cur.fetchall()
        
        return [self._row_to_draft_entry(row) for row in rows]
    
    def _row_to_draft_entry(self, row) -> DraftEntry:
        return DraftEntry(
            id=row['id'],
            match_id=row['match_id'],
            debit_account=row['debit_account'],
            credit_account=row['credit_account'],
            amount=row['amount'],
            currency=row['currency'],
            description=row['description'],
            posting_date=row['posting_date'],
            confidence=row['confidence'],
            auto_generated=bool(row['auto_generated']),
            status=ApprovalStatus(row['status']),
            approved_by=row['approved_by'],
            approved_at=row['approved_at'],
            rejection_reason=row['rejection_reason'],
            posted_to_erp=bool(row['posted_to_erp']),
            erp_document_id=row['erp_document_id'],
            posted_at=row['posted_at'],
            organization_id=row['organization_id'],
            created_at=row['created_at'],
        )
    
    # ==================== AUDIT ====================
    
    def _audit(self, action: str, entity_type: str, entity_id: str, organization_id: Optional[str]):
        """Record an audit log entry."""
        log = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            organization_id=organization_id,
        )
        
        sql = self._prepare_sql("""
            INSERT INTO audit_log (id, action, entity_type, entity_id, user_id, user_email,
                                   surface, changes, metadata, organization_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                log.id, log.action, log.entity_type, log.entity_id,
                log.user_id, log.user_email, log.surface,
                json.dumps(log.changes), json.dumps(log.metadata),
                log.organization_id, log.timestamp
            ))
            conn.commit()
    
    # ==================== OAUTH TOKENS ====================
    
    def save_oauth_token(
        self,
        user_id: str,
        provider: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[str] = None,
        email: Optional[str] = None,
        scopes: Optional[str] = None,
        organization_id: Optional[str] = None
    ) -> str:
        """Save or update an OAuth token."""
        self.initialize()
        import uuid
        
        token_id = f"{provider}_{user_id}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO oauth_tokens
            (id, user_id, email, provider, access_token, refresh_token, token_type,
             expires_at, scopes, organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'Bearer', ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                token_id, user_id, email, provider, access_token, refresh_token,
                expires_at, scopes, organization_id, now, now
            ))
            conn.commit()
        
        return token_id
    
    def get_oauth_token(self, user_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """Get an OAuth token for a user and provider."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM oauth_tokens WHERE user_id = ? AND provider = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            row = cur.fetchone()
        
        if not row:
            return None
        return dict(row)
    
    def get_oauth_token_by_email(self, email: str, provider: str) -> Optional[Dict[str, Any]]:
        """Get an OAuth token by email and provider."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM oauth_tokens WHERE email = ? AND provider = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email, provider))
            row = cur.fetchone()
        
        if not row:
            return None
        return dict(row)
    
    def delete_oauth_token(self, user_id: str, provider: str) -> bool:
        """Delete an OAuth token."""
        self.initialize()
        sql = self._prepare_sql("DELETE FROM oauth_tokens WHERE user_id = ? AND provider = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id, provider))
            conn.commit()
            return cur.rowcount > 0
    
    def list_oauth_tokens(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all OAuth tokens, optionally filtered by provider."""
        self.initialize()
        
        if provider:
            sql = self._prepare_sql("SELECT * FROM oauth_tokens WHERE provider = ?")
            params = (provider,)
        else:
            sql = "SELECT * FROM oauth_tokens"
            params = ()
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]

    # ==================== GMAIL AUTOPILOT ====================

    def get_gmail_autopilot_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get Gmail autopilot state for a user."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM gmail_autopilot_state WHERE user_id = ?")

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id,))
            row = cur.fetchone()

        if not row:
            return None
        return dict(row)

    def list_gmail_autopilot_states(self) -> List[Dict[str, Any]]:
        """List all Gmail autopilot states."""
        self.initialize()
        sql = "SELECT * FROM gmail_autopilot_state"

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()

        return [dict(row) for row in rows]

    def save_gmail_autopilot_state(
        self,
        user_id: str,
        email: Optional[str] = None,
        last_scan_at: Optional[str] = None,
        last_history_id: Optional[str] = None,
        watch_expiration: Optional[str] = None,
        last_watch_at: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save or update Gmail autopilot state."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()

        existing = self.get_gmail_autopilot_state(user_id) or {}

        payload = {
            "user_id": user_id,
            "email": email if email is not None else existing.get("email"),
            "last_scan_at": last_scan_at if last_scan_at is not None else existing.get("last_scan_at"),
            "last_history_id": last_history_id if last_history_id is not None else existing.get("last_history_id"),
            "watch_expiration": watch_expiration if watch_expiration is not None else existing.get("watch_expiration"),
            "last_watch_at": last_watch_at if last_watch_at is not None else existing.get("last_watch_at"),
            "last_error": last_error if last_error is not None else existing.get("last_error"),
            "updated_at": now,
        }

        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO gmail_autopilot_state
            (user_id, email, last_scan_at, last_history_id, watch_expiration, last_watch_at, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                payload["user_id"],
                payload["email"],
                payload["last_scan_at"],
                payload["last_history_id"],
                payload["watch_expiration"],
                payload["last_watch_at"],
                payload["last_error"],
                payload["updated_at"],
            ))
            conn.commit()

        return payload
    
    # ==================== ERP CONNECTIONS ====================
    
    def save_erp_connection(
        self,
        organization_id: str,
        erp_type: str,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        token_expiry: Optional[str] = None,
        realm_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        base_url: Optional[str] = None,
        credentials: Optional[Dict] = None
    ) -> str:
        """Save or update an ERP connection."""
        self.initialize()
        
        conn_id = f"{erp_type}_{organization_id}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO erp_connections
            (id, organization_id, erp_type, access_token, refresh_token, token_expiry,
             realm_id, tenant_id, base_url, credentials, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                conn_id, organization_id, erp_type, access_token, refresh_token,
                token_expiry, realm_id, tenant_id, base_url,
                json.dumps(credentials) if credentials else None, now, now
            ))
            conn.commit()
        
        self._audit("connected", "erp", erp_type, organization_id)
        return conn_id
    
    def get_erp_connection(self, organization_id: str, erp_type: str) -> Optional[Dict[str, Any]]:
        """Get an ERP connection."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM erp_connections WHERE organization_id = ? AND erp_type = ? AND is_active = 1
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, erp_type))
            row = cur.fetchone()
        
        if not row:
            return None
        
        result = dict(row)
        if result.get('credentials'):
            result['credentials'] = json.loads(result['credentials'])
        return result
    
    def get_erp_connections(self, organization_id: str) -> List[Dict[str, Any]]:
        """Get all ERP connections for an organization."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM erp_connections WHERE organization_id = ? AND is_active = 1
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        
        results = []
        for row in rows:
            r = dict(row)
            if r.get('credentials'):
                r['credentials'] = json.loads(r['credentials'])
            results.append(r)
        return results
    
    def delete_erp_connection(self, organization_id: str, erp_type: str) -> bool:
        """Soft delete an ERP connection."""
        self.initialize()
        sql = self._prepare_sql("""
            UPDATE erp_connections SET is_active = 0, updated_at = ? 
            WHERE organization_id = ? AND erp_type = ?
        """)
        now = datetime.now(timezone.utc).isoformat()
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, organization_id, erp_type))
            conn.commit()
            deleted = cur.rowcount > 0
        
        if deleted:
            self._audit("disconnected", "erp", erp_type, organization_id)
        return deleted
    
    def update_erp_sync_time(self, organization_id: str, erp_type: str) -> None:
        """Update the last sync timestamp for an ERP connection."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql("""
            UPDATE erp_connections SET last_sync_at = ?, updated_at = ?
            WHERE organization_id = ? AND erp_type = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, now, organization_id, erp_type))
            conn.commit()
    
    # ==================== USERS ====================
    
    def save_user(
        self,
        email: str,
        name: Optional[str] = None,
        password_hash: Optional[str] = None,
        role: str = "member",
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> str:
        """Create or update a user."""
        self.initialize()
        import uuid
        
        if not user_id:
            user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO users
            (id, email, name, password_hash, role, organization_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                user_id, email, name, password_hash, role, organization_id, now, now
            ))
            conn.commit()
        
        return user_id
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE id = ? AND is_active = 1")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
        
        if not row:
            return None
        return dict(row)
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by email."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM users WHERE email = ? AND is_active = 1")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (email,))
            row = cur.fetchone()
        
        if not row:
            return None
        return dict(row)
    
    def get_users(self, organization_id: str) -> List[Dict[str, Any]]:
        """Get all users in an organization."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM users WHERE organization_id = ? AND is_active = 1 ORDER BY created_at
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def update_user(self, user_id: str, **kwargs) -> bool:
        """Update user fields."""
        self.initialize()
        
        allowed_fields = {'name', 'role', 'organization_id', 'is_active', 'last_login_at'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not updates:
            return False
        
        updates['updated_at'] = datetime.now(timezone.utc).isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        sql = self._prepare_sql(f"UPDATE users SET {set_clause} WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*updates.values(), user_id))
            conn.commit()
            return cur.rowcount > 0
    
    def delete_user(self, user_id: str) -> bool:
        """Soft delete a user."""
        return self.update_user(user_id, is_active=0)
    
    # ==================== ORGANIZATIONS ====================
    
    def save_organization(
        self,
        name: str,
        slug: Optional[str] = None,
        settings: Optional[Dict] = None,
        features: Optional[Dict] = None,
        subscription_tier: str = "free",
        org_id: Optional[str] = None
    ) -> str:
        """Create or update an organization."""
        self.initialize()
        import uuid
        
        if not org_id:
            org_id = str(uuid.uuid4())
        if not slug:
            slug = name.lower().replace(" ", "-").replace("_", "-")
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO organizations
            (id, name, slug, settings, features, subscription_tier, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                org_id, name, slug,
                json.dumps(settings) if settings else None,
                json.dumps(features) if features else None,
                subscription_tier, now, now
            ))
            conn.commit()
        
        return org_id
    
    def get_organization(self, org_id: str) -> Optional[Dict[str, Any]]:
        """Get an organization by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM organizations WHERE id = ? AND is_active = 1")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (org_id,))
            row = cur.fetchone()
        
        if not row:
            return None
        
        result = dict(row)
        if result.get('settings'):
            result['settings'] = json.loads(result['settings'])
        if result.get('features'):
            result['features'] = json.loads(result['features'])
        return result
    
    def get_organization_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get an organization by slug."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM organizations WHERE slug = ? AND is_active = 1")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (slug,))
            row = cur.fetchone()
        
        if not row:
            return None
        
        result = dict(row)
        if result.get('settings'):
            result['settings'] = json.loads(result['settings'])
        if result.get('features'):
            result['features'] = json.loads(result['features'])
        return result
    
    def update_organization(
        self,
        organization_id: str,
        name: Optional[str] = None,
        settings: Optional[Dict] = None,
        features: Optional[List[str]] = None,
        subscription_tier: Optional[str] = None,
    ) -> bool:
        """Update an organization's settings."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if settings is not None:
            updates.append("settings = ?")
            params.append(json.dumps(settings))
        
        if features is not None:
            updates.append("features = ?")
            params.append(json.dumps(features))
        
        if subscription_tier is not None:
            updates.append("subscription_tier = ?")
            params.append(subscription_tier)
        
        if not updates:
            return False
        
        updates.append("updated_at = ?")
        params.append(now)
        params.append(organization_id)
        
        sql = self._prepare_sql(f"UPDATE organizations SET {', '.join(updates)} WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0
    
    # ==================== SLACK THREADS ====================
    
    def save_slack_thread(
        self,
        invoice_id: str,
        channel_id: str,
        thread_ts: str,
        gmail_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        message_ts: Optional[str] = None,
    ) -> str:
        """Save a Slack thread associated with an invoice."""
        self.initialize()
        import uuid
        
        thread_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO slack_threads
            (id, invoice_id, gmail_id, channel_id, thread_ts, message_ts, status,
             organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                thread_id, invoice_id, gmail_id, channel_id, thread_ts,
                message_ts, organization_id, now, now
            ))
            conn.commit()
        
        return thread_id
    
    def get_slack_thread(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        """Get Slack thread for an invoice."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM slack_threads WHERE invoice_id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (invoice_id,))
            row = cur.fetchone()
        
        return dict(row) if row else None
    
    def get_slack_thread_by_ts(self, channel_id: str, thread_ts: str) -> Optional[Dict[str, Any]]:
        """Get Slack thread by channel and timestamp."""
        self.initialize()
        sql = self._prepare_sql(
            "SELECT * FROM slack_threads WHERE channel_id = ? AND thread_ts = ?"
        )
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (channel_id, thread_ts))
            row = cur.fetchone()
        
        return dict(row) if row else None
    
    def update_slack_thread_status(
        self,
        thread_id: str,
        status: str,
        approved_by: Optional[str] = None,
        rejection_reason: Optional[str] = None
    ) -> bool:
        """Update Slack thread approval status."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            UPDATE slack_threads 
            SET status = ?, approved_by = ?, approved_at = ?, rejection_reason = ?, updated_at = ?
            WHERE id = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                status, approved_by, now if status == 'approved' else None,
                rejection_reason, now, thread_id
            ))
            conn.commit()
            return cur.rowcount > 0
    
    # ==================== INVOICE STATUS ====================
    
    def save_invoice_status(
        self,
        gmail_id: str,
        status: str = "new",
        email_subject: Optional[str] = None,
        vendor: Optional[str] = None,
        amount: Optional[float] = None,
        currency: str = "USD",
        invoice_number: Optional[str] = None,
        due_date: Optional[str] = None,
        confidence: float = 0,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Save or update invoice status tracking."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_invoice_status(gmail_id)
        if existing:
            invoice_id = existing.get("id")
            sql = self._prepare_sql("""
                UPDATE invoice_status
                SET email_subject = ?, vendor = ?, amount = ?, currency = ?, invoice_number = ?,
                    due_date = ?, status = ?, confidence = ?, organization_id = ?, user_id = ?,
                    updated_at = ?
                WHERE gmail_id = ?
            """)
            with self.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (
                    email_subject, vendor, amount, currency, invoice_number,
                    due_date, status, confidence, organization_id, user_id,
                    now, gmail_id
                ))
                conn.commit()
            return invoice_id

        import uuid
        invoice_id = str(uuid.uuid4())

        sql = self._prepare_sql("""
            INSERT INTO invoice_status
            (id, gmail_id, email_subject, vendor, amount, currency, invoice_number,
             due_date, status, confidence, organization_id, user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                invoice_id, gmail_id, email_subject, vendor, amount, currency,
                invoice_number, due_date, status, confidence, organization_id,
                user_id, now, now
            ))
            conn.commit()

        return invoice_id
    
    def get_invoice_status(self, gmail_id: str) -> Optional[Dict[str, Any]]:
        """Get invoice status by Gmail ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM invoice_status WHERE gmail_id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (gmail_id,))
            row = cur.fetchone()
        
        return dict(row) if row else None
    
    def get_invoices_by_status(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get invoices filtered by status."""
        self.initialize()
        
        if status:
            sql = self._prepare_sql("""
                SELECT * FROM invoice_status 
                WHERE organization_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql("""
                SELECT * FROM invoice_status 
                WHERE organization_id = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, limit)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def update_invoice_status(
        self,
        gmail_id: str,
        status: str,
        **kwargs
    ) -> bool:
        """
        Update invoice status and optional fields.
        
        Valid statuses: new, pending_approval, approved, posted, rejected
        Optional kwargs: approved_by, erp_vendor_id, erp_bill_id, slack_thread_id, rejection_reason
        """
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        
        # Build update fields
        updates = {"status": status, "updated_at": now}
        
        allowed_fields = {
            'approved_by', 'approved_at', 'posted_at', 'erp_vendor_id',
            'erp_bill_id', 'slack_thread_id', 'rejection_reason'
        }
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                updates[key] = value
        
        # Auto-set timestamps based on status
        if status == 'approved' and 'approved_at' not in updates:
            updates['approved_at'] = now
        if status == 'posted' and 'posted_at' not in updates:
            updates['posted_at'] = now
        
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        sql = self._prepare_sql(f"UPDATE invoice_status SET {set_clause} WHERE gmail_id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*updates.values(), gmail_id))
            conn.commit()
            return cur.rowcount > 0
    
    def get_invoice_pipeline(self, organization_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get all invoices grouped by status (pipeline view)."""
        self.initialize()
        
        sql = self._prepare_sql("""
            SELECT * FROM invoice_status 
            WHERE organization_id = ?
            ORDER BY 
                CASE status
                    WHEN 'new' THEN 1
                    WHEN 'pending_approval' THEN 2
                    WHEN 'approved' THEN 3
                    WHEN 'posted' THEN 4
                    WHEN 'rejected' THEN 5
                END,
                created_at DESC
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        
        pipeline = {
            "new": [],
            "pending_approval": [],
            "approved": [],
            "posted": [],
            "rejected": [],
        }
        
        for row in rows:
            invoice = dict(row)
            status = invoice.get("status", "new")
            if status in pipeline:
                pipeline[status].append(invoice)
        
        return pipeline
    
    # ==================== STATS ====================
    
    def get_stats(self, organization_id: str) -> Dict[str, Any]:
        """Get summary statistics for an organization."""
        self.initialize()
        
        with self.connect() as conn:
            cur = conn.cursor()
            
            # Transaction counts
            cur.execute(self._prepare_sql(
                "SELECT status, COUNT(*) FROM transactions WHERE organization_id = ? GROUP BY status"
            ), (organization_id,))
            tx_counts = dict(cur.fetchall())
            
            # Exception counts
            cur.execute(self._prepare_sql(
                "SELECT status, COUNT(*) FROM exceptions WHERE organization_id = ? GROUP BY status"
            ), (organization_id,))
            exc_counts = dict(cur.fetchall())
            
            # Draft counts
            cur.execute(self._prepare_sql(
                "SELECT status, COUNT(*) FROM draft_entries WHERE organization_id = ? GROUP BY status"
            ), (organization_id,))
            draft_counts = dict(cur.fetchall())
            
            # Match rate
            total_tx = sum(tx_counts.values())
            matched_tx = tx_counts.get('matched', 0)
            match_rate = (matched_tx / total_tx * 100) if total_tx > 0 else 0
        
        return {
            "transactions": tx_counts,
            "exceptions": exc_counts,
            "drafts": draft_counts,
            "match_rate": round(match_rate, 1),
            "total_transactions": total_tx,
            "open_exceptions": exc_counts.get('open', 0),
            "pending_approvals": draft_counts.get('pending', 0),
        }


    # ==================== AP INVOICES ====================
    
    def save_ap_invoice(
        self,
        vendor_name: str,
        amount: float,
        organization_id: str,
        invoice_number: str = "",
        vendor_id: str = "",
        currency: str = "USD",
        due_date: Optional[str] = None,
        gl_code: str = "",
        description: str = "",
        status: str = "pending",
        email_id: str = "",
        thread_id: str = "",
        po_number: str = "",
        confidence: float = 0.0,
        invoice_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save an AP invoice."""
        self.initialize()
        import uuid
        
        if not invoice_id:
            invoice_id = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO ap_invoices
            (id, invoice_number, vendor_name, vendor_id, amount, currency, due_date,
             gl_code, description, status, email_id, thread_id, po_number, confidence,
             organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                invoice_id, invoice_number, vendor_name, vendor_id, amount, currency,
                due_date, gl_code, description, status, email_id, thread_id, po_number,
                confidence, organization_id, now, now
            ))
            conn.commit()
        
        return {
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
            "vendor_name": vendor_name,
            "vendor_id": vendor_id,
            "amount": amount,
            "currency": currency,
            "due_date": due_date,
            "gl_code": gl_code,
            "description": description,
            "status": status,
            "email_id": email_id,
            "thread_id": thread_id,
            "po_number": po_number,
            "confidence": confidence,
            "organization_id": organization_id,
            "created_at": now,
            "updated_at": now,
        }
    
    def get_ap_invoices(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get AP invoices with optional status filter."""
        self.initialize()
        
        if status:
            sql = self._prepare_sql("""
                SELECT * FROM ap_invoices WHERE organization_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql("""
                SELECT * FROM ap_invoices WHERE organization_id = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, limit)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def get_ap_invoice(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        """Get AP invoice by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_invoices WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (invoice_id,))
            row = cur.fetchone()
        
        return dict(row) if row else None
    
    def update_ap_invoice_status(self, invoice_id: str, status: str) -> bool:
        """Update AP invoice status."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql("UPDATE ap_invoices SET status = ?, updated_at = ? WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (status, now, invoice_id))
            conn.commit()
            return cur.rowcount > 0
    
    # ==================== AP PAYMENTS ====================
    
    def save_ap_payment(
        self,
        invoice_id: str,
        vendor_id: str,
        vendor_name: str,
        amount: float,
        organization_id: str,
        currency: str = "USD",
        method: str = "ach",
        status: str = "pending",
        payment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save an AP payment."""
        self.initialize()
        import uuid
        
        if not payment_id:
            payment_id = f"PAY-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO ap_payments
            (id, invoice_id, vendor_id, vendor_name, amount, currency, method, status,
             organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                payment_id, invoice_id, vendor_id, vendor_name, amount, currency,
                method, status, organization_id, now, now
            ))
            conn.commit()
        
        return {
            "payment_id": payment_id,
            "invoice_id": invoice_id,
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "amount": amount,
            "currency": currency,
            "method": method,
            "status": status,
            "organization_id": organization_id,
            "created_at": now,
        }
    
    def get_ap_payments(
        self,
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get AP payments with optional status filter."""
        self.initialize()
        
        if status:
            sql = self._prepare_sql("""
                SELECT * FROM ap_payments WHERE organization_id = ? AND status = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, status, limit)
        else:
            sql = self._prepare_sql("""
                SELECT * FROM ap_payments WHERE organization_id = ?
                ORDER BY created_at DESC LIMIT ?
            """)
            params = (organization_id, limit)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def get_ap_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Get AP payment by ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM ap_payments WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (payment_id,))
            row = cur.fetchone()
        
        return dict(row) if row else None
    
    def update_ap_payment(self, payment_id: str, **kwargs) -> bool:
        """Update AP payment fields."""
        self.initialize()
        
        allowed = {'status', 'batch_id', 'scheduled_date', 'sent_at', 'completed_at'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        
        updates['updated_at'] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        sql = self._prepare_sql(f"UPDATE ap_payments SET {set_clause} WHERE id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*updates.values(), payment_id))
            conn.commit()
            return cur.rowcount > 0
    
    def get_ap_payments_summary(self, organization_id: str) -> Dict[str, Any]:
        """Get AP payments summary statistics."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT status, COUNT(*) as count, SUM(amount) as total
            FROM ap_payments WHERE organization_id = ?
            GROUP BY status
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        
        result = {"pending": 0, "scheduled": 0, "processing_amount": 0, "completed_30d": 0, "total_pending_amount": 0}
        for row in rows:
            status = row['status']
            count = row['count']
            total = row['total'] or 0
            if status == 'pending':
                result['pending'] = count
                result['total_pending_amount'] = total
            elif status == 'scheduled':
                result['scheduled'] = count
            elif status == 'processing':
                result['processing_amount'] = total
            elif status == 'completed':
                result['completed_30d'] = count
        
        return result
    
    # ==================== GL CORRECTIONS ====================
    
    def save_gl_correction(
        self,
        invoice_id: str,
        vendor: str,
        original_gl: str,
        corrected_gl: str,
        organization_id: str,
        reason: str = "",
        corrected_by: str = "",
    ) -> Dict[str, Any]:
        """Save a GL correction."""
        self.initialize()
        import uuid
        
        correction_id = f"COR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT INTO gl_corrections
            (id, invoice_id, vendor, original_gl, corrected_gl, reason, was_correct,
             confidence_impact, corrected_by, organization_id, corrected_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0.05, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                correction_id, invoice_id, vendor, original_gl, corrected_gl,
                reason, corrected_by, organization_id, now
            ))
            conn.commit()
        
        return {
            "correction_id": correction_id,
            "invoice_id": invoice_id,
            "vendor": vendor,
            "original_gl": original_gl,
            "corrected_gl": corrected_gl,
            "reason": reason,
            "corrected_at": now,
            "was_correct": False,
            "confidence_impact": 0.05,
        }
    
    def get_gl_corrections(
        self,
        organization_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get GL corrections."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM gl_corrections WHERE organization_id = ?
            ORDER BY corrected_at DESC LIMIT ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id, limit))
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def get_gl_stats(self, organization_id: str) -> Dict[str, Any]:
        """Get GL correction statistics."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT COUNT(*) as total, 
                   SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct,
                   COUNT(DISTINCT vendor) as vendors
            FROM gl_corrections WHERE organization_id = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            row = cur.fetchone()
        
        total = row['total'] or 0
        correct = row['correct'] or 0
        vendors = row['vendors'] or 0
        accuracy = correct / total if total > 0 else 0.0
        
        return {
            "accuracy": accuracy,
            "total_corrections": total,
            "learned_rules": vendors,
        }
    
    # ==================== RECURRING RULES ====================
    
    def save_recurring_rule(
        self,
        vendor_name: str,
        organization_id: str,
        vendor_pattern: str = "",
        frequency: str = "monthly",
        expected_amount: float = 0.0,
        amount_tolerance: float = 0.1,
        gl_code: str = "",
        auto_approve: bool = False,
        rule_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a recurring rule."""
        self.initialize()
        import uuid
        
        if not rule_id:
            rule_id = f"REC-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        if not vendor_pattern:
            vendor_pattern = vendor_name.lower()
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO recurring_rules
            (id, vendor_name, vendor_pattern, frequency, expected_amount, amount_tolerance,
             gl_code, auto_approve, status, organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                rule_id, vendor_name, vendor_pattern, frequency, expected_amount,
                amount_tolerance, gl_code, 1 if auto_approve else 0, organization_id, now, now
            ))
            conn.commit()
        
        return {
            "rule_id": rule_id,
            "vendor_name": vendor_name,
            "vendor_pattern": vendor_pattern,
            "frequency": frequency,
            "expected_amount": expected_amount,
            "amount_tolerance": amount_tolerance,
            "gl_code": gl_code,
            "auto_approve": auto_approve,
            "status": "active",
            "created_at": now,
        }
    
    def get_recurring_rules(self, organization_id: str) -> List[Dict[str, Any]]:
        """Get recurring rules."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT * FROM recurring_rules WHERE organization_id = ? AND status = 'active'
            ORDER BY created_at DESC
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            rows = cur.fetchall()
        
        return [dict(row) for row in rows]
    
    def delete_recurring_rule(self, rule_id: str) -> bool:
        """Delete (deactivate) a recurring rule."""
        self.initialize()
        sql = self._prepare_sql("UPDATE recurring_rules SET status = 'inactive', updated_at = ? WHERE id = ?")
        now = datetime.now(timezone.utc).isoformat()
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (now, rule_id))
            conn.commit()
            return cur.rowcount > 0
    
    def get_recurring_summary(self, organization_id: str) -> Dict[str, Any]:
        """Get recurring rules summary."""
        self.initialize()
        sql = self._prepare_sql("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN frequency = 'monthly' THEN expected_amount ELSE 0 END) as monthly_spend,
                   SUM(CASE WHEN auto_approve = 1 THEN 1 ELSE 0 END) as auto_approved
            FROM recurring_rules WHERE organization_id = ? AND status = 'active'
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (organization_id,))
            row = cur.fetchone()
        
        return {
            "active_rules": row['total'] or 0,
            "monthly_spend": row['monthly_spend'] or 0,
            "due_this_week": 0,  # Would need date logic
            "auto_approved": row['auto_approved'] or 0,
        }
    
    # ==================== ERP SYNC TRACKING ====================
    
    def save_erp_sync_tracking(
        self,
        thread_id: str,
        organization_id: str,
        email_id: str = "",
        invoice_id: str = "",
        erp_type: str = "",
        erp_id: str = "",
        erp_status: str = "",
        synced: bool = False,
    ) -> Dict[str, Any]:
        """Save or update ERP sync tracking."""
        self.initialize()
        import uuid
        
        sync_id = f"SYNC-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        
        sql = self._prepare_sql("""
            INSERT OR REPLACE INTO erp_sync_tracking
            (id, thread_id, email_id, invoice_id, erp_type, erp_id, erp_status, synced,
             last_synced, organization_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                sync_id, thread_id, email_id, invoice_id, erp_type, erp_id,
                erp_status, 1 if synced else 0, now if synced else None,
                organization_id, now, now
            ))
            conn.commit()
        
        return {
            "sync_id": sync_id,
            "thread_id": thread_id,
            "email_id": email_id,
            "invoice_id": invoice_id,
            "erp_type": erp_type,
            "erp_id": erp_id,
            "erp_status": erp_status,
            "synced": synced,
            "last_synced": now if synced else None,
        }
    
    def get_erp_sync_by_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Get ERP sync tracking by thread ID."""
        self.initialize()
        sql = self._prepare_sql("SELECT * FROM erp_sync_tracking WHERE thread_id = ?")
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (thread_id,))
            row = cur.fetchone()
        
        if not row:
            return {"thread_id": thread_id, "synced": False, "erp_type": None, "erp_id": None, "last_synced": None}
        return dict(row)
    
    def update_erp_sync_status(self, thread_id: str, synced: bool, erp_id: str = "", erp_status: str = "") -> bool:
        """Update ERP sync status."""
        self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        sql = self._prepare_sql("""
            UPDATE erp_sync_tracking 
            SET synced = ?, erp_id = ?, erp_status = ?, last_synced = ?, updated_at = ?
            WHERE thread_id = ?
        """)
        
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (1 if synced else 0, erp_id, erp_status, now if synced else None, now, thread_id))
            conn.commit()
            return cur.rowcount > 0


# Global instance
_db: Optional[ClearledgrDB] = None


def get_db() -> ClearledgrDB:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = ClearledgrDB()
    return _db
