"""
Clearledgr Engine

The central brain that orchestrates all operations.
All surfaces call this engine - it's the single point of control.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from clearledgr.core.database import get_db, ClearledgrDB
from clearledgr.core.models import (
    Transaction, Match, Exception as ExceptionModel, DraftEntry, FinanceEmail,
    TransactionSource, TransactionStatus, ExceptionType, ExceptionPriority, ApprovalStatus
)


class ClearledgrEngine:
    """
    The central engine that all surfaces connect to.
    
    Gmail Extension → Engine → Database
    Sheets Add-on  → Engine → Database  
    Slack App      → Engine → Database
    
    This ensures:
    1. Single source of truth
    2. Consistent business logic
    3. Unified audit trail
    4. Cross-surface sync
    """
    
    def __init__(self, db: Optional[ClearledgrDB] = None):
        self.db = db or get_db()
        self.db.initialize()
    
    # ==================== FINANCE EMAILS ====================
    
    def detect_finance_email(
        self,
        gmail_id: str,
        subject: str,
        sender: str,
        received_at: str,
        email_type: str,
        confidence: float,
        organization_id: str,
        user_id: str,
        vendor: Optional[str] = None,
        amount: Optional[float] = None,
        invoice_number: Optional[str] = None,
    ) -> FinanceEmail:
        """
        Record a detected finance email from Gmail extension.
        This is the entry point for Gmail → Central Store.
        """
        email = FinanceEmail(
            gmail_id=gmail_id,
            subject=subject,
            sender=sender,
            received_at=received_at,
            email_type=email_type,
            confidence=confidence,
            vendor=vendor,
            amount=amount,
            invoice_number=invoice_number,
            organization_id=organization_id,
            user_id=user_id,
        )
        return self.db.save_finance_email(email)
    
    def get_finance_emails(
        self, 
        organization_id: str,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get detected finance emails for display in any surface."""
        emails = self.db.get_finance_emails(organization_id, status, limit)
        return [e.to_dict() for e in emails]
    
    def process_finance_email(
        self,
        email_id: str,
        organization_id: str,
        user_id: str,
    ) -> Transaction:
        """
        Process a finance email into a transaction.
        Called when user clicks "Process" in Gmail or Sheets.
        """
        # Get the email
        emails = self.db.get_finance_emails(organization_id)
        email = next((e for e in emails if e.id == email_id), None)
        if not email:
            raise ValueError(f"Email {email_id} not found")
        
        # Create transaction from email
        tx = Transaction(
            amount=email.amount or 0,
            currency=email.currency,
            date=email.received_at,
            description=email.subject,
            reference=email.invoice_number,
            source=TransactionSource.EMAIL,
            source_id=email.gmail_id,
            vendor=email.vendor,
            organization_id=organization_id,
        )
        tx = self.db.save_transaction(tx)
        
        # Update email status
        email.status = "processed"
        email.processed_at = datetime.now(timezone.utc).isoformat()
        email.transaction_id = tx.id
        self.db.save_finance_email(email)
        
        return tx
    
    # ==================== TRANSACTIONS ====================
    
    def add_transaction(
        self,
        amount: float,
        currency: str,
        date: str,
        description: str,
        source: str,
        organization_id: str,
        reference: Optional[str] = None,
        source_id: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> Transaction:
        """Add a transaction from any source."""
        tx = Transaction(
            amount=amount,
            currency=currency,
            date=date,
            description=description,
            reference=reference,
            source=TransactionSource(source),
            source_id=source_id,
            vendor=vendor,
            organization_id=organization_id,
        )
        return self.db.save_transaction(tx)
    
    def get_transactions(
        self,
        organization_id: str,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get transactions for display."""
        txs = self.db.get_transactions(organization_id, status, source, limit)
        return [tx.to_dict() for tx in txs]
    
    def get_pending_transactions(self, organization_id: str) -> List[Dict[str, Any]]:
        """Get transactions awaiting reconciliation."""
        return self.get_transactions(organization_id, status="pending")
    
    # ==================== RECONCILIATION ====================
    
    def run_reconciliation(
        self,
        organization_id: str,
        gateway_transactions: List[Dict],
        bank_transactions: List[Dict],
        internal_transactions: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Run reconciliation and store results.
        Called from Sheets or scheduled job.
        """
        from clearledgr.services.multi_factor_scoring import MultiFactorScorer
        
        # Save incoming transactions (with currency fallback)
        for tx_data in gateway_transactions:
            self.add_transaction(
                amount=tx_data.get("amount", 0),
                currency=tx_data.get("currency", "EUR"),
                date=tx_data.get("date", ""),
                description=tx_data.get("description", ""),
                reference=tx_data.get("reference"),
                source="gateway",
                organization_id=organization_id,
            )
        
        for tx_data in bank_transactions:
            self.add_transaction(
                amount=tx_data.get("amount", 0),
                currency=tx_data.get("currency", "EUR"),
                date=tx_data.get("date", ""),
                description=tx_data.get("description", ""),
                reference=tx_data.get("reference"),
                source="bank", 
                organization_id=organization_id,
            )
        
        if internal_transactions:
            for tx_data in internal_transactions:
                self.add_transaction(
                    amount=tx_data.get("amount", 0),
                    currency=tx_data.get("currency", "EUR"),
                    date=tx_data.get("date", ""),
                    description=tx_data.get("description", ""),
                    reference=tx_data.get("reference"),
                    source="internal",
                    organization_id=organization_id,
                )
        
        # Run matching
        scorer = MultiFactorScorer()
        matches = []
        exceptions = []
        
        # Get all pending transactions
        gateway_txs = self.db.get_transactions(organization_id, status="pending", source="gateway")
        bank_txs = self.db.get_transactions(organization_id, status="pending", source="bank")
        
        # Match gateway to bank
        for g_tx in gateway_txs:
            best_match = None
            best_score = 0
            
            for b_tx in bank_txs:
                score = scorer.score_match(
                    {
                        "amount": g_tx.amount,
                        "date": g_tx.date,
                        "description": g_tx.description,
                        "reference": g_tx.reference,
                    },
                    {
                        "amount": b_tx.amount,
                        "date": b_tx.date,
                        "description": b_tx.description,
                        "reference": b_tx.reference,
                    }
                )
                
                if score.total_score > best_score:
                    best_score = score.total_score
                    best_match = b_tx
            
            if best_match and best_score >= 70:  # Lower threshold for demo - production would be 80
                # Create match
                match = Match(
                    gateway_id=g_tx.id,
                    bank_id=best_match.id,
                    score=best_score,
                    confidence=best_score / 100,
                    organization_id=organization_id,
                )
                self.db.save_match(match)
                matches.append(match)
                
                # Update transaction statuses
                g_tx.status = TransactionStatus.MATCHED
                g_tx.matched_with = [best_match.id]
                g_tx.match_score = best_score
                self.db.save_transaction(g_tx)
                
                best_match.status = TransactionStatus.MATCHED
                best_match.matched_with = [g_tx.id]
                best_match.match_score = best_score
                self.db.save_transaction(best_match)
                
                # Auto-generate draft if high confidence
                if best_score >= 90:
                    draft = DraftEntry(
                        match_id=match.id,
                        amount=g_tx.amount,
                        currency=g_tx.currency,
                        description=f"Auto-matched: {g_tx.description}",
                        confidence=best_score / 100,
                        organization_id=organization_id,
                    )
                    self.db.save_draft_entry(draft)
            else:
                # Create exception
                exc = ExceptionModel(
                    transaction_id=g_tx.id,
                    transaction_source=TransactionSource.GATEWAY,
                    type=ExceptionType.NO_MATCH,
                    priority=self._determine_priority(g_tx.amount),
                    amount=g_tx.amount,
                    currency=g_tx.currency,
                    vendor=g_tx.vendor,
                    organization_id=organization_id,
                )
                self.db.save_exception(exc)
                exceptions.append(exc)
                
                g_tx.status = TransactionStatus.EXCEPTION
                self.db.save_transaction(g_tx)
        
        return {
            "matches": len(matches),
            "exceptions": len(exceptions),
            "match_rate": len(matches) / max(len(gateway_txs), 1) * 100,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
    
    def _determine_priority(self, amount: float) -> ExceptionPriority:
        """Determine exception priority based on amount."""
        if amount >= 10000:
            return ExceptionPriority.CRITICAL
        elif amount >= 5000:
            return ExceptionPriority.HIGH
        elif amount >= 1000:
            return ExceptionPriority.MEDIUM
        return ExceptionPriority.LOW
    
    def get_matches(
        self,
        organization_id: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get reconciliation matches."""
        matches = self.db.get_matches(organization_id, limit)
        return [m.to_dict() for m in matches]
    
    # ==================== EXCEPTIONS ====================
    
    def get_exceptions(
        self,
        organization_id: str,
        status: str = "open",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get exceptions for review."""
        exceptions = self.db.get_exceptions(organization_id, status, limit)
        return [e.to_dict() for e in exceptions]
    
    def resolve_exception(
        self,
        exception_id: str,
        organization_id: str,
        user_id: str,
        resolution_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve an exception."""
        exceptions = self.db.get_exceptions(organization_id, status="open")
        exc = next((e for e in exceptions if e.id == exception_id), None)
        if not exc:
            raise ValueError(f"Exception {exception_id} not found")
        
        exc.status = "resolved"
        exc.resolved_by = user_id
        exc.resolved_at = datetime.now(timezone.utc).isoformat()
        exc.resolution_notes = resolution_notes
        self.db.save_exception(exc)
        
        return exc.to_dict()
    
    # ==================== DRAFT ENTRIES ====================
    
    def get_draft_entries(
        self,
        organization_id: str,
        status: str = "pending",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get draft journal entries."""
        drafts = self.db.get_draft_entries(organization_id, status, limit)
        return [d.to_dict() for d in drafts]
    
    def approve_draft(
        self,
        draft_id: str,
        organization_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Approve a draft journal entry."""
        drafts = self.db.get_draft_entries(organization_id, status="pending")
        draft = next((d for d in drafts if d.id == draft_id), None)
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        
        draft.status = ApprovalStatus.APPROVED
        draft.approved_by = user_id
        draft.approved_at = datetime.now(timezone.utc).isoformat()
        self.db.save_draft_entry(draft)
        
        return draft.to_dict()
    
    def reject_draft(
        self,
        draft_id: str,
        organization_id: str,
        user_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Reject a draft journal entry."""
        drafts = self.db.get_draft_entries(organization_id, status="pending")
        draft = next((d for d in drafts if d.id == draft_id), None)
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        
        draft.status = ApprovalStatus.REJECTED
        draft.approved_by = user_id
        draft.approved_at = datetime.now(timezone.utc).isoformat()
        draft.rejection_reason = reason
        self.db.save_draft_entry(draft)
        
        return draft.to_dict()
    
    # ==================== STATS ====================
    
    def get_stats(self, organization_id: str) -> Dict[str, Any]:
        """Get summary statistics."""
        return self.db.get_stats(organization_id)
    
    def get_dashboard_data(self, organization_id: str) -> Dict[str, Any]:
        """Get all data needed for dashboard display."""
        db_stats = self.db.get_stats(organization_id)
        emails = self.db.get_finance_emails(organization_id, limit=50)
        exceptions = self.db.get_exceptions(organization_id, status="open", limit=10)
        drafts = self.db.get_draft_entries(organization_id, status="pending", limit=10)
        matches = self.db.get_matches(organization_id, limit=10)
        
        return {
            # Stats in the format the surfaces expect
            "email_count": len(emails),
            "matched_count": db_stats.get("transactions", {}).get("matched", 0),
            "exception_count": db_stats.get("open_exceptions", 0),
            "pending_count": db_stats.get("pending_approvals", 0),
            "matched_transactions": db_stats.get("transactions", {}).get("matched", 0),
            "open_exceptions": db_stats.get("open_exceptions", 0),
            "pending_drafts": db_stats.get("pending_approvals", 0),
            "match_rate": db_stats.get("match_rate", 0),
            # Detailed stats
            "stats": db_stats,
            # Recent data
            "recent_emails": [e.to_dict() for e in emails[:5]],
            "recent_exceptions": [e.to_dict() for e in exceptions[:5]],
            "recent_drafts": [d.to_dict() for d in drafts[:5]],
            "recent_matches": [m.to_dict() for m in matches[:5]],
            "recent_activity": self._build_recent_activity(emails, exceptions, drafts),
        }
    
    def _build_recent_activity(
        self, 
        emails: List[FinanceEmail], 
        exceptions: List[ExceptionModel], 
        drafts: List[DraftEntry]
    ) -> List[Dict]:
        """Build recent activity feed."""
        
        activities = []
        
        for email in emails[:3]:
            activities.append({
                "type": "email",
                "description": f"Email detected: {email.subject[:40]}...",
                "timestamp": email.created_at,
            })
        
        for exc in exceptions[:3]:
            activities.append({
                "type": "exception",
                "description": f"Exception: {exc.type.value} - {exc.vendor or 'Unknown'}",
                "timestamp": exc.created_at,
            })
        
        for draft in drafts[:3]:
            activities.append({
                "type": "draft",
                "description": f"Draft entry: {draft.description[:40]}..." if draft.description else "Draft entry created",
                "timestamp": draft.created_at,
            })
        
        # Sort by timestamp
        activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return activities[:10]


# Global instance
_engine: Optional[ClearledgrEngine] = None


def get_engine() -> ClearledgrEngine:
    """Get the global engine instance."""
    global _engine
    if _engine is None:
        _engine = ClearledgrEngine()
    return _engine
