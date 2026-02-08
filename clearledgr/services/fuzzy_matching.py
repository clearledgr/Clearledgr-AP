"""
Fuzzy Matching Service for Clearledgr v1

Provides intelligent matching beyond exact/tolerance comparisons:
- Vendor name fuzzy matching (handles variations)
- Transaction description similarity
- Reference ID partial matching
- Amount clustering for related transactions
"""
from typing import Dict, List, Tuple, Optional
import re
from difflib import SequenceMatcher


def normalize_vendor(vendor: str) -> str:
    """
    Normalize vendor name for comparison.
    
    Examples:
        "STRIPE INC" -> "stripe"
        "Stripe.com" -> "stripe"
        "STRIPE PAYMENTS UK" -> "stripe payments uk"
    """
    if not vendor:
        return ""
    
    # Lowercase
    normalized = vendor.lower().strip()
    
    # Remove common suffixes
    suffixes = [
        ' inc', ' inc.', ' llc', ' ltd', ' ltd.', ' limited',
        ' corp', ' corp.', ' corporation', ' co', ' co.',
        ' gmbh', ' ag', ' plc', ' pty', ' sa', ' nv', ' bv',
        '.com', '.io', '.co', '.org', '.net'
    ]
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    
    # Remove special characters but keep spaces
    normalized = re.sub(r'[^\w\s]', '', normalized)
    
    # Collapse multiple spaces
    normalized = ' '.join(normalized.split())
    
    return normalized


def vendor_similarity(vendor1: str, vendor2: str) -> float:
    """
    Calculate similarity between two vendor names.
    
    Returns:
        float: Similarity score 0.0 to 1.0
    """
    if not vendor1 or not vendor2:
        return 0.0
    
    norm1 = normalize_vendor(vendor1)
    norm2 = normalize_vendor(vendor2)
    
    # Exact match after normalization
    if norm1 == norm2:
        return 1.0
    
    # One contains the other
    if norm1 in norm2 or norm2 in norm1:
        shorter = min(len(norm1), len(norm2))
        longer = max(len(norm1), len(norm2))
        return shorter / longer * 0.95
    
    # Token-based similarity (word overlap)
    tokens1 = set(norm1.split())
    tokens2 = set(norm2.split())
    
    if tokens1 and tokens2:
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        jaccard = len(intersection) / len(union)
        
        # If first words match, boost score
        if list(tokens1)[0] == list(tokens2)[0] if tokens1 and tokens2 else False:
            jaccard = min(1.0, jaccard + 0.2)
    else:
        jaccard = 0.0
    
    # Character-level similarity
    sequence_ratio = SequenceMatcher(None, norm1, norm2).ratio()
    
    # Combined score (weighted average)
    return max(jaccard, sequence_ratio)


def fuzzy_match_vendors(
    source_vendor: str,
    candidates: List[Dict],
    vendor_field: str = "vendor",
    threshold: float = 0.7
) -> List[Tuple[Dict, float]]:
    """
    Find candidates with similar vendor names.
    
    Args:
        source_vendor: Vendor to match
        candidates: List of candidate transactions
        vendor_field: Field name containing vendor in candidates
        threshold: Minimum similarity threshold
    
    Returns:
        List of (candidate, similarity_score) tuples, sorted by score
    """
    matches = []
    
    for candidate in candidates:
        candidate_vendor = candidate.get(vendor_field, "") or candidate.get("description", "")
        similarity = vendor_similarity(source_vendor, candidate_vendor)
        
        if similarity >= threshold:
            matches.append((candidate, similarity))
    
    # Sort by similarity descending
    matches.sort(key=lambda x: x[1], reverse=True)
    
    return matches


def reference_id_similarity(ref1: str, ref2: str) -> float:
    """
    Calculate similarity between reference IDs.
    Handles partial matches, prefix/suffix variations.
    
    Examples:
        "INV-2024-001" vs "2024-001" -> 0.8
        "TXN12345" vs "12345" -> 0.7
    """
    if not ref1 or not ref2:
        return 0.0
    
    # Normalize
    clean1 = re.sub(r'[^A-Za-z0-9]', '', ref1.upper())
    clean2 = re.sub(r'[^A-Za-z0-9]', '', ref2.upper())
    
    # Exact match
    if clean1 == clean2:
        return 1.0
    
    # One contains the other
    if clean1 in clean2:
        return len(clean1) / len(clean2) * 0.9
    if clean2 in clean1:
        return len(clean2) / len(clean1) * 0.9
    
    # Extract numeric portions
    nums1 = re.findall(r'\d+', clean1)
    nums2 = re.findall(r'\d+', clean2)
    
    if nums1 and nums2:
        # Compare longest numeric sequences
        longest1 = max(nums1, key=len)
        longest2 = max(nums2, key=len)
        
        if longest1 == longest2:
            return 0.85
        if longest1 in longest2 or longest2 in longest1:
            return 0.7
    
    # Sequence matching
    return SequenceMatcher(None, clean1, clean2).ratio()


def amount_cluster_match(
    amount: float,
    candidates: List[Dict],
    amount_field: str = "amount",
    tolerance_pct: float = 0.5,
    include_related: bool = True
) -> List[Tuple[Dict, str, float]]:
    """
    Find candidates with matching or related amounts.
    
    Handles:
    - Exact matches
    - Within tolerance
    - Split transactions (amount is sum of multiple)
    - Partial payments
    
    Returns:
        List of (candidate, match_type, score) tuples
    """
    if not amount or amount == 0:
        return []
    
    matches = []
    tolerance = abs(amount) * (tolerance_pct / 100)
    
    for candidate in candidates:
        cand_amount = candidate.get(amount_field, 0) or 0
        if not cand_amount:
            continue
        
        diff = abs(amount - cand_amount)
        
        if diff == 0:
            # Exact match
            matches.append((candidate, "exact", 1.0))
        elif diff <= tolerance:
            # Within tolerance
            score = 1.0 - (diff / tolerance) * 0.1
            matches.append((candidate, "tolerance", score))
        elif include_related:
            # Check for round number relationships
            ratio = amount / cand_amount if cand_amount != 0 else 0
            
            # Check if one is multiple of other (split/combined transactions)
            if 0.9 <= ratio <= 1.1:
                matches.append((candidate, "approximate", 0.8))
            elif 1.9 <= ratio <= 2.1:
                matches.append((candidate, "double", 0.6))
            elif 0.45 <= ratio <= 0.55:
                matches.append((candidate, "half", 0.6))
    
    # Sort by score
    matches.sort(key=lambda x: x[2], reverse=True)
    
    return matches


def smart_match_score(
    source: Dict,
    candidate: Dict,
    config: Dict = None
) -> Tuple[float, List[Dict]]:
    """
    Calculate comprehensive match score using multiple factors.
    
    Args:
        source: Source transaction
        candidate: Candidate transaction to compare
        config: Optional config with weights
    
    Returns:
        (total_score, reasoning_steps)
    """
    config = config or {}
    
    # Default weights
    weights = {
        "amount": config.get("weight_amount", 0.35),
        "date": config.get("weight_date", 0.25),
        "vendor": config.get("weight_vendor", 0.25),
        "reference": config.get("weight_reference", 0.15)
    }
    
    reasoning = []
    total_score = 0.0
    
    # Amount matching
    source_amount = source.get("amount") or source.get("net_amount") or 0
    cand_amount = candidate.get("amount") or candidate.get("net_amount") or 0
    
    if source_amount and cand_amount:
        amount_matches = amount_cluster_match(source_amount, [candidate])
        if amount_matches:
            match_type, score = amount_matches[0][1], amount_matches[0][2]
            total_score += score * weights["amount"]
            reasoning.append({
                "factor": "Amount",
                "observation": f"${source_amount:,.2f} vs ${cand_amount:,.2f} ({match_type})",
                "impact": "positive" if score >= 0.8 else "neutral",
                "score": score
            })
        else:
            reasoning.append({
                "factor": "Amount",
                "observation": f"${source_amount:,.2f} vs ${cand_amount:,.2f} (no match)",
                "impact": "negative",
                "score": 0
            })
    
    # Date matching
    source_date = source.get("date")
    cand_date = candidate.get("date")
    
    if source_date and cand_date:
        try:
            from datetime import datetime
            if isinstance(source_date, str):
                sd = datetime.strptime(source_date[:10], "%Y-%m-%d")
            else:
                sd = source_date
            if isinstance(cand_date, str):
                cd = datetime.strptime(cand_date[:10], "%Y-%m-%d")
            else:
                cd = cand_date
            
            days_diff = abs((sd - cd).days)
            date_window = config.get("date_window_days", 3)
            
            if days_diff == 0:
                date_score = 1.0
                date_obs = "Same date"
            elif days_diff <= date_window:
                date_score = 1.0 - (days_diff / (date_window * 2))
                date_obs = f"{days_diff} day(s) apart"
            else:
                date_score = 0.0
                date_obs = f"{days_diff} days apart (outside window)"
            
            total_score += date_score * weights["date"]
            reasoning.append({
                "factor": "Date",
                "observation": date_obs,
                "impact": "positive" if date_score >= 0.5 else "negative",
                "score": date_score
            })
        except:
            pass
    
    # Vendor matching
    source_vendor = source.get("vendor") or source.get("description") or ""
    cand_vendor = candidate.get("vendor") or candidate.get("description") or ""
    
    if source_vendor and cand_vendor:
        vendor_score = vendor_similarity(source_vendor, cand_vendor)
        total_score += vendor_score * weights["vendor"]
        
        reasoning.append({
            "factor": "Vendor",
            "observation": f"'{source_vendor}' vs '{cand_vendor}' ({vendor_score*100:.0f}% similar)",
            "impact": "positive" if vendor_score >= 0.7 else "neutral" if vendor_score >= 0.4 else "negative",
            "score": vendor_score
        })
    
    # Reference ID matching
    source_ref = source.get("txn_id") or source.get("reference") or source.get("invoice_number") or ""
    cand_ref = candidate.get("txn_id") or candidate.get("bank_txn_id") or candidate.get("internal_id") or ""
    
    if source_ref and cand_ref:
        ref_score = reference_id_similarity(source_ref, cand_ref)
        if ref_score > 0.5:
            total_score += ref_score * weights["reference"]
            reasoning.append({
                "factor": "Reference",
                "observation": f"'{source_ref}' vs '{cand_ref}' ({ref_score*100:.0f}% similar)",
                "impact": "positive" if ref_score >= 0.7 else "neutral",
                "score": ref_score
            })
    
    return total_score, reasoning


def find_best_matches(
    source: Dict,
    candidates: List[Dict],
    config: Dict = None,
    top_n: int = 5,
    min_score: float = 0.3
) -> List[Tuple[Dict, float, List[Dict]]]:
    """
    Find best matching candidates for a source transaction.
    
    Returns:
        List of (candidate, score, reasoning) tuples, sorted by score
    """
    results = []
    
    for candidate in candidates:
        score, reasoning = smart_match_score(source, candidate, config)
        
        if score >= min_score:
            results.append((candidate, score, reasoning))
    
    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    
    return results[:top_n]

