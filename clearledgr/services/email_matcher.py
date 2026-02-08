"""
Clearledgr Email Matcher Service

Matches parsed email data to existing transactions:
- Invoice to bank transaction
- Payment confirmation to open invoice
- Auto-matching with 90-95% target accuracy
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from decimal import Decimal


class EmailMatcher:
    """
    Matches parsed email/document data to transactions.
    Target: 90-95% auto-match rate.
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.amount_tolerance_pct = self.config.get("amount_tolerance_pct", 1.0)
        self.date_window_days = self.config.get("date_window_days", 7)
    
    def match_invoice_to_transactions(
        self,
        invoice: Dict,
        bank_transactions: List[Dict],
        internal_transactions: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Match a parsed invoice to bank and internal transactions.
        
        Args:
            invoice: Parsed invoice data
            bank_transactions: List of bank transactions
            internal_transactions: Optional list of internal/GL entries
            
        Returns:
            Match result with confidence score
        """
        internal_transactions = internal_transactions or []
        
        invoice_amount = self._get_amount(invoice)
        invoice_date = self._parse_date(invoice.get('date') or invoice.get('primary_date'))
        invoice_vendor = (invoice.get('vendor') or '').lower()
        invoice_number = invoice.get('invoice_number') or invoice.get('primary_invoice')
        
        if not invoice_amount:
            return {
                "matched": False,
                "reason": "no_amount",
                "message": "Invoice has no extractable amount"
            }
        
        # Find bank transaction matches
        bank_matches = self._find_amount_matches(
            invoice_amount,
            invoice_date,
            invoice_vendor,
            bank_transactions,
            "bank"
        )
        
        # Find internal transaction matches
        internal_matches = self._find_amount_matches(
            invoice_amount,
            invoice_date,
            invoice_vendor,
            internal_transactions,
            "internal"
        )
        
        # Determine best match
        best_match = None
        match_type = None
        
        if bank_matches and internal_matches:
            # 3-way match possible
            best_match = {
                "bank": bank_matches[0],
                "internal": internal_matches[0]
            }
            match_type = "3-way-match"
        elif bank_matches:
            best_match = {"bank": bank_matches[0]}
            match_type = "2-way-match-invoice-bank"
        elif internal_matches:
            best_match = {"internal": internal_matches[0]}
            match_type = "2-way-match-invoice-internal"
        
        if best_match:
            # Calculate confidence
            confidence = self._calculate_match_confidence(
                invoice, best_match, match_type
            )
            
            return {
                "matched": True,
                "match_type": match_type,
                "confidence": confidence,
                "auto_approve": confidence >= 0.9,
                "invoice": {
                    "amount": invoice_amount,
                    "date": invoice_date.isoformat() if invoice_date else None,
                    "vendor": invoice.get('vendor'),
                    "invoice_number": invoice_number
                },
                "matches": best_match,
                "all_bank_candidates": bank_matches[:5],
                "all_internal_candidates": internal_matches[:5]
            }
        else:
            return {
                "matched": False,
                "reason": "no_match",
                "message": f"No matching transaction found for amount {invoice_amount}",
                "invoice": {
                    "amount": invoice_amount,
                    "date": invoice_date.isoformat() if invoice_date else None,
                    "vendor": invoice.get('vendor'),
                    "invoice_number": invoice_number
                },
                "suggestions": self._get_match_suggestions(invoice_amount, bank_transactions)
            }
    
    def match_payment_to_invoice(
        self,
        payment: Dict,
        open_invoices: List[Dict]
    ) -> Dict[str, Any]:
        """
        Match a payment confirmation to open invoices.
        
        Args:
            payment: Parsed payment confirmation
            open_invoices: List of open/unpaid invoices
            
        Returns:
            Match result
        """
        payment_amount = self._get_amount(payment)
        payment_date = self._parse_date(payment.get('date'))
        payment_payer = (payment.get('payer') or '').lower()
        
        if not payment_amount:
            return {
                "matched": False,
                "reason": "no_amount",
                "message": "Payment has no extractable amount"
            }
        
        # Find matching invoices
        matches = []
        for invoice in open_invoices:
            inv_amount = self._get_amount(invoice)
            if not inv_amount:
                continue
            
            # Check amount match
            if self._amounts_match(payment_amount, inv_amount):
                score = self._score_invoice_match(payment, invoice)
                matches.append({
                    "invoice": invoice,
                    "score": score,
                    "amount_diff": abs(payment_amount - inv_amount)
                })
        
        # Sort by score
        matches.sort(key=lambda x: x['score'], reverse=True)
        
        if matches:
            best = matches[0]
            return {
                "matched": True,
                "confidence": best['score'],
                "auto_approve": best['score'] >= 0.9,
                "payment": {
                    "amount": payment_amount,
                    "date": payment_date.isoformat() if payment_date else None,
                    "transaction_id": payment.get('transaction_id'),
                    "payer": payment.get('payer')
                },
                "matched_invoice": best['invoice'],
                "all_candidates": [m['invoice'] for m in matches[:5]]
            }
        else:
            return {
                "matched": False,
                "reason": "no_matching_invoice",
                "message": f"No open invoice found for amount {payment_amount}",
                "payment": {
                    "amount": payment_amount,
                    "transaction_id": payment.get('transaction_id')
                }
            }
    
    def get_exceptions_for_vendor(
        self,
        vendor: str,
        all_invoices: List[Dict],
        all_transactions: List[Dict]
    ) -> Dict[str, Any]:
        """
        Get all unmatched items for a vendor.
        
        Args:
            vendor: Vendor name
            all_invoices: All invoices
            all_transactions: All transactions
            
        Returns:
            Exception summary for vendor
        """
        vendor_lower = vendor.lower()
        
        # Filter to vendor
        vendor_invoices = [
            inv for inv in all_invoices
            if vendor_lower in (inv.get('vendor') or '').lower()
        ]
        
        vendor_transactions = [
            txn for txn in all_transactions
            if vendor_lower in (txn.get('counterparty') or txn.get('description') or '').lower()
        ]
        
        # Find unmatched
        unmatched_invoices = []
        unmatched_transactions = []
        
        matched_txn_ids = set()
        
        for invoice in vendor_invoices:
            inv_amount = self._get_amount(invoice)
            found_match = False
            
            for txn in vendor_transactions:
                txn_amount = self._get_amount(txn)
                txn_id = txn.get('id') or txn.get('transaction_id')
                
                if txn_id in matched_txn_ids:
                    continue
                
                if self._amounts_match(inv_amount, txn_amount):
                    matched_txn_ids.add(txn_id)
                    found_match = True
                    break
            
            if not found_match:
                unmatched_invoices.append(invoice)
        
        for txn in vendor_transactions:
            txn_id = txn.get('id') or txn.get('transaction_id')
            if txn_id not in matched_txn_ids:
                unmatched_transactions.append(txn)
        
        total_unmatched = sum(
            self._get_amount(inv) or 0 for inv in unmatched_invoices
        )
        
        return {
            "vendor": vendor,
            "unmatched_invoices": unmatched_invoices,
            "unmatched_transactions": unmatched_transactions,
            "unmatched_invoice_count": len(unmatched_invoices),
            "unmatched_transaction_count": len(unmatched_transactions),
            "total_unmatched_amount": total_unmatched,
            "has_exceptions": len(unmatched_invoices) > 0 or len(unmatched_transactions) > 0
        }
    
    def _find_amount_matches(
        self,
        target_amount: float,
        target_date: datetime,
        vendor: str,
        transactions: List[Dict],
        source: str
    ) -> List[Dict]:
        """Find transactions matching the target amount."""
        matches = []
        
        for txn in transactions:
            txn_amount = self._get_amount(txn)
            if not txn_amount:
                continue
            
            if not self._amounts_match(target_amount, txn_amount):
                continue
            
            # Calculate match score
            score = 0.5  # Base score for amount match
            
            # Date proximity bonus
            txn_date = self._parse_date(txn.get('date'))
            if target_date and txn_date:
                days_diff = abs((target_date - txn_date).days)
                if days_diff <= self.date_window_days:
                    score += 0.2 * (1 - days_diff / self.date_window_days)
            
            # Vendor match bonus
            txn_counterparty = (
                txn.get('counterparty') or 
                txn.get('description') or 
                ''
            ).lower()
            if vendor and vendor in txn_counterparty:
                score += 0.2
            
            # Exact amount bonus
            if abs(target_amount - txn_amount) < 0.01:
                score += 0.1
            
            matches.append({
                "transaction": txn,
                "source": source,
                "score": min(score, 1.0),
                "amount": txn_amount,
                "amount_diff": abs(target_amount - txn_amount)
            })
        
        # Sort by score
        matches.sort(key=lambda x: x['score'], reverse=True)
        return matches
    
    def _calculate_match_confidence(
        self,
        invoice: Dict,
        matches: Dict,
        match_type: str
    ) -> float:
        """Calculate overall match confidence."""
        base_confidence = 0.6 if match_type == "3-way-match" else 0.4
        
        # Add confidence from individual matches
        for source, match in matches.items():
            if isinstance(match, dict) and 'score' in match:
                base_confidence += match['score'] * 0.2
        
        return min(base_confidence, 0.99)
    
    def _score_invoice_match(self, payment: Dict, invoice: Dict) -> float:
        """Score how well a payment matches an invoice."""
        score = 0.5  # Base for amount match
        
        # Date proximity
        payment_date = self._parse_date(payment.get('date'))
        invoice_date = self._parse_date(invoice.get('date') or invoice.get('due_date'))
        
        if payment_date and invoice_date:
            days_diff = (payment_date - invoice_date).days
            if 0 <= days_diff <= 30:  # Payment within 30 days of invoice
                score += 0.2
            elif -7 <= days_diff < 0:  # Early payment
                score += 0.15
        
        # Payer/vendor match
        payer = (payment.get('payer') or '').lower()
        vendor = (invoice.get('vendor') or invoice.get('customer') or '').lower()
        if payer and vendor and (payer in vendor or vendor in payer):
            score += 0.2
        
        # Reference match
        payment_ref = payment.get('transaction_id') or ''
        invoice_ref = invoice.get('invoice_number') or ''
        if payment_ref and invoice_ref and (payment_ref in invoice_ref or invoice_ref in payment_ref):
            score += 0.1
        
        return min(score, 1.0)
    
    def _get_match_suggestions(
        self,
        amount: float,
        transactions: List[Dict]
    ) -> List[Dict]:
        """Get suggestions for potential matches."""
        suggestions = []
        
        for txn in transactions:
            txn_amount = self._get_amount(txn)
            if not txn_amount:
                continue
            
            diff_pct = abs(amount - txn_amount) / amount * 100 if amount else 0
            
            if diff_pct <= 10:  # Within 10%
                suggestions.append({
                    "transaction": txn,
                    "amount": txn_amount,
                    "diff_pct": round(diff_pct, 2),
                    "reason": f"Amount {diff_pct:.1f}% different"
                })
        
        suggestions.sort(key=lambda x: x['diff_pct'])
        return suggestions[:5]
    
    def _get_amount(self, data: Dict) -> Optional[float]:
        """Extract amount from data dict."""
        if not data:
            return None
        
        # Try different field names
        for field in ['amount', 'primary_amount', 'total', 'net_amount', 'value']:
            val = data.get(field)
            if val is not None:
                if isinstance(val, dict):
                    val = val.get('value')
                if val is not None:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        continue
        
        return None
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string to datetime."""
        if not date_str:
            return None
        
        if isinstance(date_str, datetime):
            return date_str
        
        for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%dT%H:%M:%S']:
            try:
                return datetime.strptime(date_str[:10], fmt[:len(date_str)])
            except ValueError:
                continue
        
        return None
    
    def _amounts_match(self, amount1: float, amount2: float) -> bool:
        """Check if two amounts match within tolerance."""
        if not amount1 or not amount2:
            return False
        
        tolerance = max(amount1, amount2) * (self.amount_tolerance_pct / 100)
        return abs(amount1 - amount2) <= tolerance


# Convenience functions

def match_invoice_to_transactions(
    invoice: Dict,
    bank_transactions: List[Dict],
    internal_transactions: List[Dict] = None,
    config: Dict = None
) -> Dict[str, Any]:
    """Match invoice to transactions."""
    matcher = EmailMatcher(config)
    return matcher.match_invoice_to_transactions(
        invoice, bank_transactions, internal_transactions
    )


def match_payment_to_invoice(
    payment: Dict,
    open_invoices: List[Dict],
    config: Dict = None
) -> Dict[str, Any]:
    """Match payment to invoice."""
    matcher = EmailMatcher(config)
    return matcher.match_payment_to_invoice(payment, open_invoices)


def get_exceptions_for_vendor(
    vendor: str,
    all_invoices: List[Dict],
    all_transactions: List[Dict],
    config: Dict = None
) -> Dict[str, Any]:
    """Get exceptions for vendor."""
    matcher = EmailMatcher(config)
    return matcher.get_exceptions_for_vendor(vendor, all_invoices, all_transactions)

