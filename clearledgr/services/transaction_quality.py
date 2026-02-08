"""
Transaction Quality Service for Clearledgr v1

Provides data quality features:
- Duplicate detection
- Multi-currency normalization
- Data validation
- Anomaly detection
"""
from typing import Dict, List, Tuple, Optional, Set
from datetime import datetime, date
from decimal import Decimal
import re
from collections import defaultdict


# Exchange rates (simplified - in production, fetch real-time rates)
EXCHANGE_RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "NGN": 0.00063,  # Nigerian Naira
    "ZAR": 0.055,    # South African Rand
    "KES": 0.0077,   # Kenyan Shilling
    "GHS": 0.083,    # Ghanaian Cedi
    "XOF": 0.0016,   # West African CFA Franc
    "XAF": 0.0016,   # Central African CFA Franc
    "EGP": 0.032,    # Egyptian Pound
    "MAD": 0.099,    # Moroccan Dirham
    "TZS": 0.00040,  # Tanzanian Shilling
    "UGX": 0.00027,  # Ugandan Shilling
    "RWF": 0.00078,  # Rwandan Franc
    "ETB": 0.018,    # Ethiopian Birr
    "CHF": 1.12,     # Swiss Franc
    "SEK": 0.095,    # Swedish Krona
    "NOK": 0.094,    # Norwegian Krone
    "DKK": 0.15,     # Danish Krone
    "PLN": 0.25,     # Polish Zloty
    "CZK": 0.043,    # Czech Koruna
    "HUF": 0.0028,   # Hungarian Forint
    "RON": 0.22,     # Romanian Leu
}


def normalize_currency(
    amount: float,
    currency: str,
    target_currency: str = "EUR"
) -> Tuple[float, str]:
    """
    Normalize an amount to a target currency.
    
    Args:
        amount: Original amount
        currency: Original currency code
        target_currency: Target currency code
    
    Returns:
        (normalized_amount, conversion_note)
    """
    currency = currency.upper() if currency else "EUR"
    target_currency = target_currency.upper()
    
    if currency == target_currency:
        return amount, f"Same currency ({currency})"
    
    # Get rates
    source_rate = EXCHANGE_RATES_TO_USD.get(currency, 1.0)
    target_rate = EXCHANGE_RATES_TO_USD.get(target_currency, 1.0)
    
    # Convert: source -> USD -> target
    usd_amount = amount * source_rate
    target_amount = usd_amount / target_rate
    
    return round(target_amount, 2), f"Converted {currency} to {target_currency} at {source_rate/target_rate:.4f}"


def detect_currency(text: str) -> Optional[str]:
    """
    Detect currency from text containing amount.
    
    Examples:
        "$100" -> "USD"
        "€50" -> "EUR"
        "NGN 1000" -> "NGN"
    """
    currency_patterns = {
        "USD": [r'\$', r'USD', r'US\$'],
        "EUR": [r'€', r'EUR'],
        "GBP": [r'£', r'GBP'],
        "NGN": [r'₦', r'NGN'],
        "ZAR": [r'R(?=\d)', r'ZAR'],
        "KES": [r'KES', r'KSh'],
        "GHS": [r'GHS', r'GH₵', r'₵'],
    }
    
    text_upper = text.upper()
    
    for currency, patterns in currency_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return currency
    
    return None


def normalize_amounts_for_matching(
    transactions: List[Dict],
    amount_field: str = "amount",
    currency_field: str = "currency",
    target_currency: str = "EUR"
) -> List[Dict]:
    """
    Add normalized amounts to transactions for cross-currency matching.
    """
    for tx in transactions:
        amount = tx.get(amount_field, 0)
        currency = tx.get(currency_field) or detect_currency(str(amount)) or "EUR"
        
        normalized, note = normalize_currency(amount, currency, target_currency)
        tx["normalized_amount"] = normalized
        tx["original_currency"] = currency
        tx["currency_conversion"] = note
    
    return transactions


class DuplicateDetector:
    """
    Detects potential duplicate transactions.
    """
    
    def __init__(self, config: Dict = None):
        config = config or {}
        self.amount_tolerance = config.get("duplicate_amount_tolerance", 0.01)  # 1 cent
        self.date_window = config.get("duplicate_date_window", 1)  # Same day or adjacent
        self.require_same_vendor = config.get("duplicate_require_vendor", True)
    
    def find_duplicates(
        self,
        transactions: List[Dict],
        id_field: str = "txn_id"
    ) -> List[Dict]:
        """
        Find potential duplicate transactions.
        
        Returns:
            List of duplicate groups with reasoning
        """
        duplicates = []
        seen: Set[str] = set()
        
        # Index by amount for faster lookup
        amount_index = defaultdict(list)
        for i, tx in enumerate(transactions):
            amount = tx.get("amount") or tx.get("net_amount") or 0
            key = round(amount, 2)
            amount_index[key].append((i, tx))
        
        # Check each transaction
        for i, tx in enumerate(transactions):
            tx_id = tx.get(id_field, str(i))
            if tx_id in seen:
                continue
            
            amount = tx.get("amount") or tx.get("net_amount") or 0
            amount_key = round(amount, 2)
            
            # Find potential matches by amount
            potential_matches = []
            for key in [amount_key - 0.01, amount_key, amount_key + 0.01]:
                potential_matches.extend(amount_index.get(key, []))
            
            # Check for duplicates
            group = [tx]
            reasons = []
            
            for j, candidate in potential_matches:
                if j <= i:  # Don't compare with self or already processed
                    continue
                
                cand_id = candidate.get(id_field, str(j))
                if cand_id in seen:
                    continue
                
                is_dup, dup_reasons = self._is_duplicate(tx, candidate)
                if is_dup:
                    group.append(candidate)
                    reasons.extend(dup_reasons)
                    seen.add(cand_id)
            
            if len(group) > 1:
                seen.add(tx_id)
                duplicates.append({
                    "type": "potential_duplicate",
                    "transactions": group,
                    "count": len(group),
                    "reasons": list(set(reasons)),
                    "total_amount": sum(
                        (t.get("amount") or t.get("net_amount") or 0) for t in group
                    )
                })
        
        return duplicates
    
    def _is_duplicate(self, tx1: Dict, tx2: Dict) -> Tuple[bool, List[str]]:
        """Check if two transactions are duplicates."""
        reasons = []
        
        # Amount check
        amt1 = tx1.get("amount") or tx1.get("net_amount") or 0
        amt2 = tx2.get("amount") or tx2.get("net_amount") or 0
        
        if abs(amt1 - amt2) > self.amount_tolerance:
            return False, []
        
        reasons.append(f"Same amount: ${amt1:,.2f}")
        
        # Date check
        date1 = tx1.get("date")
        date2 = tx2.get("date")
        
        if date1 and date2:
            try:
                if isinstance(date1, str):
                    d1 = datetime.strptime(date1[:10], "%Y-%m-%d")
                else:
                    d1 = date1
                if isinstance(date2, str):
                    d2 = datetime.strptime(date2[:10], "%Y-%m-%d")
                else:
                    d2 = date2
                
                if abs((d1 - d2).days) > self.date_window:
                    return False, []
                
                if d1 == d2:
                    reasons.append("Same date")
                else:
                    reasons.append(f"Adjacent dates ({abs((d1-d2).days)} day apart)")
            except:
                pass
        
        # Vendor check (if required)
        if self.require_same_vendor:
            vendor1 = (tx1.get("vendor") or tx1.get("description") or "").lower()
            vendor2 = (tx2.get("vendor") or tx2.get("description") or "").lower()
            
            # Normalize and compare
            v1_words = set(re.sub(r'[^\w\s]', '', vendor1).split())
            v2_words = set(re.sub(r'[^\w\s]', '', vendor2).split())
            
            if not v1_words or not v2_words:
                return False, []
            
            overlap = len(v1_words & v2_words) / max(len(v1_words), len(v2_words))
            if overlap < 0.5:
                return False, []
            
            reasons.append(f"Similar vendor ({overlap*100:.0f}% match)")
        
        # Reference ID check (if available)
        ref1 = tx1.get("txn_id") or tx1.get("reference")
        ref2 = tx2.get("txn_id") or tx2.get("reference")
        
        if ref1 and ref2 and ref1 == ref2:
            reasons.append(f"Same reference ID: {ref1}")
        
        return True, reasons


def validate_transaction(tx: Dict) -> Dict:
    """
    Validate a transaction and return issues.
    """
    issues = []
    
    # Check for required fields
    amount = tx.get("amount") or tx.get("net_amount")
    if amount is None:
        issues.append({"field": "amount", "issue": "Missing amount", "severity": "error"})
    elif not isinstance(amount, (int, float, Decimal)):
        issues.append({"field": "amount", "issue": "Invalid amount type", "severity": "error"})
    
    date_val = tx.get("date")
    if not date_val:
        issues.append({"field": "date", "issue": "Missing date", "severity": "warning"})
    
    # Check for suspicious patterns
    if amount and abs(amount) > 1000000:
        issues.append({
            "field": "amount",
            "issue": f"Unusually large amount: ${abs(amount):,.2f}",
            "severity": "warning"
        })
    
    if amount and amount == 0:
        issues.append({
            "field": "amount",
            "issue": "Zero amount transaction",
            "severity": "info"
        })
    
    return {
        "valid": len([i for i in issues if i["severity"] == "error"]) == 0,
        "issues": issues
    }


def get_match_suggestions(
    unmatched_tx: Dict,
    all_candidates: List[Dict],
    config: Dict = None
) -> List[Dict]:
    """
    Get smart suggestions for an unmatched transaction.
    """
    from clearledgr.services.fuzzy_matching import find_best_matches
    
    # Find best matches even below normal threshold
    matches = find_best_matches(
        unmatched_tx,
        all_candidates,
        config=config,
        top_n=5,
        min_score=0.2  # Lower threshold for suggestions
    )
    
    suggestions = []
    for candidate, score, reasoning in matches:
        # Build explanation
        explanation_parts = []
        for step in reasoning:
            if step["impact"] == "positive":
                explanation_parts.append(f"{step['factor']}: {step['observation']}")
        
        suggestions.append({
            "candidate": candidate,
            "score": score,
            "confidence": f"{score * 100:.0f}%",
            "explanation": "; ".join(explanation_parts) if explanation_parts else "Partial match",
            "reasoning_steps": reasoning,
            "action_required": "Review and manually confirm if this is a match"
        })
    
    return suggestions

