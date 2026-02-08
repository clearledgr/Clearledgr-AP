"""
Journal Entry Generation Service for Clearledgr v1 (Autonomous Edition)

Implements auto-draft JE generation from product_spec_updated.md:
- Auto-generate at 90%+ confidence threshold
- Detect and account for fees automatically
- Generate complete debit/credit line items
- Status workflow: DRAFT → APPROVED → POSTED
- Store in CLDRAFTENTRIES sheet format
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from clearledgr.models.journal_entries import DraftJournalEntry
from clearledgr.models.reconciliation import ReconciliationMatch
from clearledgr.services.db import DB


DB_PATH = os.getenv("CLEARLEDGR_STATE_DB", os.path.join(os.getcwd(), "state.sqlite3"))

# Confidence threshold for auto-generating journal entries (per product_spec_updated.md)
AUTO_JE_CONFIDENCE_THRESHOLD = 90.0


class JournalEntryService:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db = DB(sqlite_path=db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS cl_draft_entries (
                entry_id TEXT PRIMARY KEY,
                date TEXT,
                description TEXT,
                debits TEXT,
                credits TEXT,
                confidence REAL,
                match_id TEXT,
                status TEXT,
                created_at TEXT,
                approved_by TEXT,
                approved_at TEXT,
                posted_at TEXT,
                sap_doc_number TEXT
            )
            """
        )

    def generate_draft(self, match: ReconciliationMatch) -> DraftJournalEntry:
        """
        Generate a draft journal entry from a matched pair (or group).
        This is a simplified example; in production this would map to real GL accounts.
        """
        entry_id = f"je_{uuid.uuid4().hex[:12]}"
        amount = match.bank.amount.amount
        fee = 0.0
        # Simple fee detection: if bank amount lower than GL amount, treat delta as fee
        if match.gl.amount.amount and match.gl.amount.amount > amount:
            fee = round(match.gl.amount.amount - amount, 2)

        debits = [{"account": "Cash", "amount": amount, "currency": match.bank.amount.currency}]
        credits = [{"account": "Revenue", "amount": match.gl.amount.amount, "currency": match.gl.amount.currency}]
        if fee > 0:
            debits.append({"account": "Processing Fees", "amount": fee, "currency": match.bank.amount.currency})

        je = DraftJournalEntry(
            entry_id=entry_id,
            date=match.bank.transaction_date or datetime.utcnow(),
            description=f"Auto-generated from match {match.bank.transaction_id}↔{match.gl.transaction_id}",
            debits=debits,
            credits=credits,
            confidence=match.score * 100,
            match_id=f"{match.bank.transaction_id}_{match.gl.transaction_id}",
            status="DRAFT",
            created_at=datetime.utcnow(),
        )
        self._persist(je)
        return je

    def _persist(self, je: DraftJournalEntry) -> None:
        self.db.execute(
            """
            INSERT INTO cl_draft_entries (entry_id, date, description, debits, credits, confidence, match_id, status, created_at, approved_by, approved_at, posted_at, sap_doc_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                date=excluded.date,
                description=excluded.description,
                debits=excluded.debits,
                credits=excluded.credits,
                confidence=excluded.confidence,
                match_id=excluded.match_id,
                status=excluded.status,
                created_at=excluded.created_at,
                approved_by=excluded.approved_by,
                approved_at=excluded.approved_at,
                posted_at=excluded.posted_at,
                sap_doc_number=excluded.sap_doc_number
            """,
            (
                je.entry_id,
                je.date.isoformat() if hasattr(je.date, "isoformat") else str(je.date),
                je.description,
                json.dumps(je.debits),
                json.dumps(je.credits),
                je.confidence,
                je.match_id,
                je.status,
                je.created_at.isoformat(),
                getattr(je, "approved_by", None),
                getattr(je, "approved_at", None),
                getattr(je, "posted_at", None),
                getattr(je, "sap_doc_number", None),
            ),
        )

    def list_drafts(self, status: Optional[str] = None) -> List[DraftJournalEntry]:
        if status:
            rows = self.db.fetchall(
                "SELECT entry_id, date, description, debits, credits, confidence, match_id, status, created_at, approved_by, approved_at, posted_at, sap_doc_number FROM cl_draft_entries WHERE status=?",
                (status,),
            )
        else:
            rows = self.db.fetchall(
                "SELECT entry_id, date, description, debits, credits, confidence, match_id, status, created_at, approved_by, approved_at, posted_at, sap_doc_number FROM cl_draft_entries"
            )

        drafts: List[DraftJournalEntry] = []
        for row in rows:
            drafts.append(
                DraftJournalEntry(
                    entry_id=row[0],
                    date=datetime.fromisoformat(row[1]) if row[1] else datetime.utcnow(),
                    description=row[2],
                    debits=json.loads(row[3]) if row[3] else [],
                    credits=json.loads(row[4]) if row[4] else [],
                    confidence=row[5],
                    match_id=row[6],
                    status=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else datetime.utcnow(),
                    approved_by=row[9] if len(row) > 9 else None,
                    approved_at=datetime.fromisoformat(row[10]) if len(row) > 10 and row[10] else None,
                    posted_at=datetime.fromisoformat(row[11]) if len(row) > 11 and row[11] else None,
                    sap_doc_number=row[12] if len(row) > 12 else None,
                )
            )
        return drafts

    def update_status(self, entry_id: str, status: str) -> Optional[DraftJournalEntry]:
        self.db.execute(
            "UPDATE cl_draft_entries SET status=? WHERE entry_id=?",
            (status, entry_id),
        )
        drafts = [d for d in self.list_drafts() if d.entry_id == entry_id]
        return drafts[0] if drafts else None

    def get_draft(self, entry_id: str) -> Optional[DraftJournalEntry]:
        rows = self.db.fetchall(
            "SELECT entry_id, date, description, debits, credits, confidence, match_id, status, created_at, approved_by, approved_at, posted_at, sap_doc_number FROM cl_draft_entries WHERE entry_id=?",
            (entry_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return DraftJournalEntry(
            entry_id=row[0],
            date=datetime.fromisoformat(row[1]) if row[1] else datetime.utcnow(),
            description=row[2],
            debits=json.loads(row[3]) if row[3] else [],
            credits=json.loads(row[4]) if row[4] else [],
            confidence=row[5],
            match_id=row[6],
            status=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else datetime.utcnow(),
            approved_by=row[9] if len(row) > 9 else None,
            approved_at=datetime.fromisoformat(row[10]) if len(row) > 10 and row[10] else None,
            posted_at=datetime.fromisoformat(row[11]) if len(row) > 11 and row[11] else None,
            sap_doc_number=row[12] if len(row) > 12 else None,
        )

    def generate_from_match_group(
        self,
        match_group: Dict[str, Any],
        confidence_score: float,
        gl_account_mapping: Optional[Dict[str, str]] = None,
    ) -> Optional[DraftJournalEntry]:
        """
        Generate a draft journal entry from a reconciliation match group.
        
        Per product_spec_updated.md:
        - Only generate if confidence >= 90%
        - Detect and account for fees automatically
        - Generate complete debit/credit line items
        
        Args:
            match_group: Match group from reconciliation with gateway, bank, internal items
            confidence_score: Match confidence (0-100)
            gl_account_mapping: Optional mapping of transaction types to GL accounts
            
        Returns:
            DraftJournalEntry if confidence >= 90%, None otherwise
        """
        if confidence_score < AUTO_JE_CONFIDENCE_THRESHOLD:
            return None
        
        entry_id = f"je_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
        
        gateway_items = match_group.get("gateway", [])
        bank_items = match_group.get("bank", [])
        internal_items = match_group.get("internal", [])
        
        if not gateway_items and not bank_items:
            return None
        
        # Calculate amounts
        gateway_amount = sum(abs(item.get("net_amount", 0) or item.get("amount", 0) or 0) 
                           for item in gateway_items)
        bank_amount = sum(abs(item.get("amount", 0) or 0) for item in bank_items)
        internal_amount = sum(abs(item.get("amount", 0) or 0) for item in internal_items)
        
        # Use gateway as primary or bank if no gateway
        primary_amount = gateway_amount or bank_amount
        
        # Detect fees (difference between gateway gross and bank net)
        fee_amount = 0.0
        if gateway_amount > 0 and bank_amount > 0 and gateway_amount > bank_amount:
            fee_amount = round(gateway_amount - bank_amount, 2)
        
        # Get currency
        currency = "EUR"
        if gateway_items:
            currency = gateway_items[0].get("currency", "EUR")
        elif bank_items:
            currency = bank_items[0].get("currency", "EUR")
        
        # Get date
        entry_date = datetime.utcnow()
        if bank_items and bank_items[0].get("date"):
            try:
                date_str = bank_items[0]["date"]
                if isinstance(date_str, str):
                    entry_date = datetime.fromisoformat(date_str.split("T")[0])
            except (ValueError, TypeError):
                pass
        
        # Build transaction IDs for reference
        gateway_ids = [item.get("txn_id", "") for item in gateway_items if item.get("txn_id")]
        bank_ids = [item.get("bank_txn_id", "") for item in bank_items if item.get("bank_txn_id")]
        match_id = f"{'_'.join(gateway_ids[:2])}|{'_'.join(bank_ids[:2])}"
        
        # Default GL account mapping
        accounts = gl_account_mapping or {
            "cash": "1010",
            "accounts_receivable": "1200",
            "revenue": "4000",
            "processing_fees": "5250",
        }
        
        # Build debits and credits
        # Standard payment reconciliation entry:
        # DR: Cash (bank amount)
        # DR: Processing Fees (if detected)
        # CR: Accounts Receivable (gateway/gross amount)
        
        debits = [
            {
                "account": accounts.get("cash", "1010"),
                "account_name": "Cash",
                "amount": bank_amount,
                "currency": currency,
            }
        ]
        
        if fee_amount > 0:
            debits.append({
                "account": accounts.get("processing_fees", "5250"),
                "account_name": "Payment Processing Fees",
                "amount": fee_amount,
                "currency": currency,
            })
        
        credits = [
            {
                "account": accounts.get("accounts_receivable", "1200"),
                "account_name": "Accounts Receivable",
                "amount": gateway_amount or bank_amount,
                "currency": currency,
            }
        ]
        
        # Build description
        is_split = match_group.get("match_type") == "split_payment"
        if is_split:
            description = f"Split payment reconciliation - {len(gateway_items)} gateway txns - Auto-generated"
        elif fee_amount > 0:
            description = f"Payment reconciliation with ${fee_amount:.2f} fee - Auto-generated"
        else:
            description = "Payment reconciliation - Auto-generated"
        
        je = DraftJournalEntry(
            entry_id=entry_id,
            date=entry_date,
            description=description,
            debits=debits,
            credits=credits,
            confidence=confidence_score,
            match_id=match_id,
            status="DRAFT",
            created_at=datetime.utcnow(),
        )
        
        self._persist(je)
        return je
    
    def auto_generate_from_reconciliation(
        self,
        reconciliation_result: Dict[str, Any],
        gl_account_mapping: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Auto-generate draft journal entries from reconciliation results.
        
        Only generates for matches with confidence >= 90%.
        
        Args:
            reconciliation_result: Full reconciliation result with groups
            gl_account_mapping: Optional GL account mapping
            
        Returns:
            {
                "generated_count": int,
                "skipped_count": int,
                "total_amount": float,
                "entries": List[DraftJournalEntry],
            }
        """
        groups = reconciliation_result.get("groups", [])
        reconciled = reconciliation_result.get("reconciled", [])
        
        generated = []
        skipped = 0
        total_amount = 0.0
        
        # Process from groups if available
        if groups:
            for i, group in enumerate(groups):
                # Get confidence from reconciled output if available
                confidence = 85.0  # Default
                if i < len(reconciled):
                    confidence = reconciled[i].get("confidence", 85.0)
                
                je = self.generate_from_match_group(group, confidence, gl_account_mapping)
                if je:
                    generated.append(je)
                    total_amount += sum(d.get("amount", 0) for d in je.debits)
                else:
                    skipped += 1
        
        # Or process from reconciled output directly
        elif reconciled:
            for rec in reconciled:
                confidence = rec.get("confidence", 85.0)
                if confidence < AUTO_JE_CONFIDENCE_THRESHOLD:
                    skipped += 1
                    continue
                
                # Build minimal group from reconciled data
                group = {
                    "gateway": [{"net_amount": rec.get("amount_gateway", 0), 
                                "date": rec.get("date_gateway")}],
                    "bank": [{"amount": rec.get("amount_bank", 0),
                             "date": rec.get("date_bank")}],
                    "internal": [],
                    "match_type": rec.get("status", ""),
                }
                
                je = self.generate_from_match_group(group, confidence, gl_account_mapping)
                if je:
                    generated.append(je)
                    total_amount += sum(d.get("amount", 0) for d in je.debits)
        
        return {
            "generated_count": len(generated),
            "skipped_count": skipped,
            "total_amount": round(total_amount, 2),
            "entries": generated,
            "average_confidence": sum(je.confidence for je in generated) / len(generated) if generated else 0,
        }
    
    def export_for_sheets(self, status: Optional[str] = None) -> List[List[Any]]:
        """
        Export drafts for CLDRAFTENTRIES sheet.
        
        Returns header row + data rows matching spec format.
        """
        headers = [
            "entry_id", "date", "description", "debit_accounts", "credit_accounts",
            "total_debits", "total_credits", "confidence", "match_group_id",
            "status", "sap_doc_number", "created_at", "approved_by", "approved_at", "posted_at"
        ]
        
        drafts = self.list_drafts(status=status)
        rows = [headers]
        
        for d in drafts:
            total_debits = sum(item.get("amount", 0) for item in d.debits)
            total_credits = sum(item.get("amount", 0) for item in d.credits)
            
            rows.append([
                d.entry_id,
                d.date.isoformat() if hasattr(d.date, "isoformat") else str(d.date),
                d.description,
                json.dumps(d.debits),
                json.dumps(d.credits),
                total_debits,
                total_credits,
                d.confidence,
                d.match_id,
                d.status,
                getattr(d, "sap_doc_number", ""),
                d.created_at.isoformat() if d.created_at else "",
                getattr(d, "approved_by", "") or "",
                getattr(d, "approved_at", "").isoformat() if getattr(d, "approved_at", None) else "",
                getattr(d, "posted_at", "").isoformat() if getattr(d, "posted_at", None) else "",
            ])
        
        return rows
