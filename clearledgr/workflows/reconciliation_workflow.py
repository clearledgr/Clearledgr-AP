"""
Reconciliation Workflow

The end-to-end flow:
1. Bank statement arrives in Gmail
2. Parse transactions from attachment
3. Fetch gateway transactions (Stripe/Flutterwave)
4. Match gateway to bank
5. Generate journal entries
6. Send Slack notification with exceptions

This is the core of what Clearledgr does.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from clearledgr.services.bank_statement_parser import parse_bank_statement
from clearledgr.services.multi_factor_scoring import MultiFactorScorer
from clearledgr.core.engine import get_engine

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""
    organization_id: str
    run_id: str
    timestamp: str
    
    # Counts
    bank_transactions: int = 0
    gateway_transactions: int = 0
    matched: int = 0
    exceptions: int = 0
    
    # Amounts
    total_bank_amount: float = 0.0
    total_gateway_amount: float = 0.0
    matched_amount: float = 0.0
    unmatched_amount: float = 0.0
    
    # Details
    matches: List[Dict[str, Any]] = field(default_factory=list)
    exception_details: List[Dict[str, Any]] = field(default_factory=list)
    draft_entries: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metrics
    match_rate: float = 0.0
    processing_time_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "summary": {
                "bank_transactions": self.bank_transactions,
                "gateway_transactions": self.gateway_transactions,
                "matched": self.matched,
                "exceptions": self.exceptions,
                "match_rate": self.match_rate,
            },
            "amounts": {
                "total_bank": self.total_bank_amount,
                "total_gateway": self.total_gateway_amount,
                "matched": self.matched_amount,
                "unmatched": self.unmatched_amount,
            },
            "matches": self.matches,
            "exceptions": self.exception_details,
            "draft_entries": self.draft_entries,
            "processing_time_ms": self.processing_time_ms,
        }


class ReconciliationWorkflow:
    """
    Orchestrates the full reconciliation workflow.
    
    This is what runs when:
    1. A bank statement email is detected in Gmail
    2. The daily scheduled reconciliation runs
    3. User manually triggers reconciliation
    
    Uses org-specific configuration for thresholds and GL mappings.
    """
    
    def __init__(self, organization_id: str):
        self.organization_id = organization_id
        self.engine = get_engine()
        self.scorer = MultiFactorScorer()
        
        # Load org-specific configuration
        from clearledgr.core.org_config import get_or_create_config
        self.org_config = get_or_create_config(organization_id)
    
    async def run(
        self,
        bank_statement_content: Optional[str] = None,
        bank_statement_type: str = "csv",
        gateway: str = "stripe",
        gateway_api_key: Optional[str] = None,
        currency: str = "EUR",
        internal_records: Optional[List[Dict[str, Any]]] = None,
        three_way: bool = False,
    ) -> ReconciliationResult:
        """
        Run full reconciliation workflow.
        
        Supports 2-way and 3-way reconciliation:
        
        2-way: Gateway settlements ↔ Bank deposits
        - Stripe payout → Bank statement
        - Paystack settlement → Bank statement
        
        3-way: Gateway transactions ↔ Bank deposits ↔ Internal records
        - Customer pays via Paystack (internal record: NGN 50,000)
        - Paystack settles to bank (bank: NGN 49,250 after fees)  
        - Your system recorded the payment (internal: NGN 50,000)
        
        Args:
            bank_statement_content: Bank statement file content (CSV or PDF text)
            bank_statement_type: "csv" or "pdf"
            gateway: Payment gateway ("stripe", "flutterwave", "paystack")
            gateway_api_key: API key for gateway
            currency: Default currency (EUR, NGN, KES, GHS, ZAR, USD)
            internal_records: Your internal transaction records for 3-way matching
            three_way: Enable 3-way reconciliation (gateway + bank + internal)
        
        Returns:
            ReconciliationResult with all details
        """
        import uuid
        import time
        
        start_time = time.time()
        run_id = str(uuid.uuid4())[:8]
        
        logger.info(f"Starting reconciliation run {run_id} for {self.organization_id}")
        
        result = ReconciliationResult(
            organization_id=self.organization_id,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        
        # Step 1: Parse bank statement
        bank_transactions = []
        if bank_statement_content:
            logger.info("Parsing bank statement...")
            bank_transactions = parse_bank_statement(
                content=bank_statement_content,
                file_type=bank_statement_type,
                currency=currency,
            )
            logger.info(f"Parsed {len(bank_transactions)} bank transactions")
        else:
            # Get existing bank transactions from database
            bank_transactions = self.engine.get_transactions(
                self.organization_id,
                source="bank",
                status="pending",
            )
            logger.info(f"Found {len(bank_transactions)} pending bank transactions in database")
        
        result.bank_transactions = len(bank_transactions)
        result.total_bank_amount = sum(abs(tx.get("amount", 0)) for tx in bank_transactions)
        
        # Step 2: Fetch gateway transactions
        gateway_transactions = await self._fetch_gateway_transactions(
            gateway=gateway,
            api_key=gateway_api_key,
        )
        
        result.gateway_transactions = len(gateway_transactions)
        result.total_gateway_amount = sum(abs(tx.get("amount", 0)) for tx in gateway_transactions)
        
        # Step 3: Run matching
        logger.info("Running multi-factor matching...")
        matches, exceptions = self._match_transactions(
            gateway_transactions=gateway_transactions,
            bank_transactions=bank_transactions,
        )
        
        result.matched = len(matches)
        result.exceptions = len(exceptions)
        result.matches = matches
        result.exception_details = exceptions
        result.matched_amount = sum(m.get("amount", 0) for m in matches)
        result.unmatched_amount = result.total_gateway_amount - result.matched_amount
        
        # Calculate match rate
        if result.gateway_transactions > 0:
            result.match_rate = (result.matched / result.gateway_transactions) * 100
        
        # Step 4: Generate draft journal entries for high-confidence matches
        logger.info("Generating draft journal entries...")
        draft_entries = self._generate_draft_entries(matches)
        result.draft_entries = draft_entries
        
        # Step 5: Store results
        self._store_results(result)
        
        result.processing_time_ms = int((time.time() - start_time) * 1000)
        
        logger.info(
            f"Reconciliation complete: {result.matched}/{result.gateway_transactions} matched "
            f"({result.match_rate:.1f}%), {result.exceptions} exceptions"
        )
        
        return result
    
    async def _fetch_gateway_transactions(
        self,
        gateway: str,
        api_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch transactions from payment gateway."""
        
        if gateway.lower() == "stripe":
            try:
                from clearledgr.services.stripe_client import get_stripe_payouts
                transactions = get_stripe_payouts(api_key=api_key, days=30)
                logger.info(f"Fetched {len(transactions)} Stripe payouts")
                return transactions
            except Exception as e:
                logger.warning(f"Could not fetch from Stripe: {e}")
                return self.engine.get_transactions(
                    self.organization_id,
                    source="gateway",
                    status="pending",
                )
        
        elif gateway.lower() == "flutterwave":
            try:
                from clearledgr.services.flutterwave_client import get_flutterwave_transfers
                transactions = await get_flutterwave_transfers(secret_key=api_key, days=30)
                logger.info(f"Fetched {len(transactions)} Flutterwave transfers")
                return transactions
            except Exception as e:
                logger.warning(f"Could not fetch from Flutterwave: {e}")
                return self.engine.get_transactions(
                    self.organization_id,
                    source="gateway",
                    status="pending",
                )
        
        elif gateway.lower() == "paystack":
            try:
                from clearledgr.services.paystack_client import get_paystack_settlements
                # For bank reconciliation, we use settlements (what hits the bank)
                transactions = await get_paystack_settlements(secret_key=api_key, days=30)
                logger.info(f"Fetched {len(transactions)} Paystack settlements")
                return transactions
            except Exception as e:
                logger.warning(f"Could not fetch from Paystack: {e}")
                return self.engine.get_transactions(
                    self.organization_id,
                    source="gateway",
                    status="pending",
                )
        
        else:
            logger.warning(f"Unknown gateway: {gateway}, using database transactions")
            return self.engine.get_transactions(
                self.organization_id,
                source="gateway",
                status="pending",
            )
    
    def _match_transactions(
        self,
        gateway_transactions: List[Dict[str, Any]],
        bank_transactions: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Match gateway transactions to bank transactions.
        
        Returns:
            Tuple of (matches, exceptions)
        """
        matches = []
        exceptions = []
        
        # Track which bank transactions have been matched
        matched_bank_ids = set()
        
        for gw_tx in gateway_transactions:
            best_match = None
            best_score = 0
            best_breakdown = None
            
            for bank_tx in bank_transactions:
                # Skip if already matched
                bank_id = bank_tx.get("id", str(hash(str(bank_tx))))
                if bank_id in matched_bank_ids:
                    continue
                
                # Score the match
                score = self.scorer.score_match(
                    source_txn=gw_tx,
                    target_txn=bank_tx,
                )
                
                if score.total_score > best_score:
                    best_score = score.total_score
                    best_match = bank_tx
                    best_breakdown = score
            
            # Use org-specific thresholds
            review_threshold = self.org_config.thresholds.review_required
            auto_threshold = self.org_config.thresholds.auto_match
            
            if best_match and best_score >= review_threshold:
                # Record match
                bank_id = best_match.get("id", str(hash(str(best_match))))
                matched_bank_ids.add(bank_id)
                
                # Detect fee
                gw_amount = abs(gw_tx.get("amount", 0))
                bank_amount = abs(best_match.get("amount", 0))
                fee = gw_amount - bank_amount if gw_amount > bank_amount else 0
                
                # Determine if auto-approved or needs review
                needs_review = best_score < auto_threshold
                is_critical = self.org_config.is_critical_amount(gw_amount)
                
                matches.append({
                    "gateway_transaction": gw_tx,
                    "bank_transaction": best_match,
                    "score": best_score,
                    "confidence": best_score / 100,
                    "amount": gw_amount,
                    "net_amount": bank_amount,
                    "fee": fee,
                    "needs_review": needs_review or is_critical,
                    "is_critical_amount": is_critical,
                    "score_breakdown": {
                        "amount": best_breakdown.amount_score,
                        "date": best_breakdown.date_score,
                        "description": best_breakdown.description_score,
                        "reference": best_breakdown.reference_score,
                    },
                })
            else:
                # No match found - exception
                exceptions.append({
                    "transaction": gw_tx,
                    "type": "no_match",
                    "reason": f"Best score was {best_score}, below threshold {review_threshold}",
                    "best_candidate": best_match,
                    "best_score": best_score,
                    "amount": abs(gw_tx.get("amount", 0)),
                    "priority": self._determine_priority(abs(gw_tx.get("amount", 0))),
                })
        
        return matches, exceptions
    
    def _determine_priority(self, amount: float) -> str:
        """Determine exception priority based on org-specific amount thresholds."""
        critical = self.org_config.thresholds.critical_amount
        high = self.org_config.thresholds.high_amount
        
        if amount >= critical:
            return "critical"
        elif amount >= high:
            return "high"
        elif amount >= high / 5:  # Medium is 1/5 of high threshold
            return "medium"
        return "low"
    
    def _generate_draft_entries(
        self,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Generate draft journal entries for high-confidence matches."""
        draft_entries = []
        
        # Use org-specific threshold for auto-generating JE
        je_threshold = self.org_config.thresholds.auto_approve_je / 100
        
        for match in matches:
            confidence = match.get("confidence", 0)
            
            # Only generate for high-confidence matches (uses org threshold)
            if confidence < je_threshold:
                continue
            
            amount = match.get("amount", 0)
            net_amount = match.get("net_amount", 0)
            fee = match.get("fee", 0)
            
            gw_tx = match.get("gateway_transaction", {})
            description = gw_tx.get("description", "Gateway payment reconciliation")
            
            # Use org-specific GL account mappings
            cash_account = self.org_config.get_gl_account("cash") or "1000"
            cash_name = self.org_config.gl_mappings.get("cash", {})
            cash_name = cash_name.account_name if hasattr(cash_name, "account_name") else "Cash"
            
            ar_account = self.org_config.get_gl_account("accounts_receivable") or "1200"
            ar_name = self.org_config.gl_mappings.get("accounts_receivable", {})
            ar_name = ar_name.account_name if hasattr(ar_name, "account_name") else "Accounts Receivable"
            
            fee_account = self.org_config.get_gl_account("payment_fees") or "6800"
            fee_name = self.org_config.gl_mappings.get("payment_fees", {})
            fee_name = fee_name.account_name if hasattr(fee_name, "account_name") else "Payment Processing Fees"
            
            entry = {
                "date": gw_tx.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                "description": f"{description} - Auto-generated",
                "confidence": confidence,
                "match_id": f"match_{hash(str(match)) % 100000}",
                "needs_review": match.get("needs_review", False),
                "lines": [
                    {
                        "account": cash_account,
                        "account_name": cash_name,
                        "debit": net_amount,
                        "credit": 0,
                    },
                ],
                "status": "draft",
            }
            
            if fee > 0:
                entry["lines"].append({
                    "account": fee_account,
                    "account_name": fee_name,
                    "debit": fee,
                    "credit": 0,
                })
            
            entry["lines"].append({
                "account": ar_account,
                "account_name": ar_name,
                "debit": 0,
                "credit": amount,
            })
            
            draft_entries.append(entry)
        
        return draft_entries
    
    def _store_results(self, result: ReconciliationResult):
        """Store reconciliation results in database."""
        # Store matches
        for match in result.matches:
            gw_tx = match.get("gateway_transaction", {})
            bank_tx = match.get("bank_transaction", {})
            
            self.engine.add_transaction(
                amount=match.get("amount", 0),
                currency=gw_tx.get("currency", "EUR"),
                date=gw_tx.get("date", ""),
                description=gw_tx.get("description", ""),
                source="gateway",
                organization_id=self.organization_id,
                reference=gw_tx.get("reference") or gw_tx.get("id"),
            )
        
        # Store exceptions
        for exc in result.exception_details:
            tx = exc.get("transaction", {})
            self.engine.add_transaction(
                amount=exc.get("amount", 0),
                currency=tx.get("currency", "EUR"),
                date=tx.get("date", ""),
                description=tx.get("description", ""),
                source="gateway",
                organization_id=self.organization_id,
                reference=tx.get("reference") or tx.get("id"),
            )


async def run_reconciliation(
    organization_id: str,
    bank_statement_content: Optional[str] = None,
    bank_statement_type: str = "csv",
    gateway: str = "stripe",
    gateway_api_key: Optional[str] = None,
    currency: str = "EUR",
) -> Dict[str, Any]:
    """
    Convenience function to run reconciliation.
    
    This is what gets called from:
    - Gmail extension when bank statement detected
    - Scheduled daily job
    - Manual trigger from Sheets or Slack
    """
    workflow = ReconciliationWorkflow(organization_id)
    result = await workflow.run(
        bank_statement_content=bank_statement_content,
        bank_statement_type=bank_statement_type,
        gateway=gateway,
        gateway_api_key=gateway_api_key,
        currency=currency,
    )
    return result.to_dict()
