"""
Reconciliation Engine for Clearledgr v1 (Autonomous Edition)

Handles loading and reconciling payment gateway, bank, and internal transaction data.
Uses the 100-point multi-factor scoring system from product_spec_updated.md:
- Amount Match: 0-40 points
- Date Proximity: 0-30 points  
- Description Similarity: 0-20 points
- Reference Match: 0-10 points

Thresholds:
- Auto-match: 80+ points
- Auto-draft JE: 90+ points

Also supports optimal (Hungarian) matching for globally optimal transaction pairing,
with split payment detection for many-to-one matching scenarios.
"""
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from clearledgr.services.csv_parser import parse_csv
from clearledgr.services.llm import generate_variance_explanation
from clearledgr.services.explainability import ReconciliationExplainer, ReasoningChain
from clearledgr.services.transaction_quality import (
    DuplicateDetector, normalize_amounts_for_matching, get_match_suggestions
)
from clearledgr.services.fuzzy_matching import find_best_matches, vendor_similarity
from clearledgr.services.optimal_matching import OptimalReconciler, HungarianMatcher
from clearledgr.services.multi_factor_scoring import (
    MultiFactorScorer, ScoreBreakdown, MatchConfidence
)
from clearledgr.services.pattern_learning import PatternLearningService
from clearledgr.services.exception_priority import (
    ExceptionPriorityClassifier, classify_exceptions, ExceptionType
)
import requests


def load_sources(
    config: Dict,
    payment_gateway_csv: bytes,
    bank_csv: bytes,
    internal_csv: bytes
) -> Dict[str, List[Dict]]:
    """
    Load and normalize all three CSV sources using parse_csv.
    
    Args:
        config: Configuration dict with 'mappings' key containing:
               - payment_gateway: mapping dict
               - bank: mapping dict
               - internal: mapping dict
        payment_gateway_csv: Payment gateway CSV file bytes
        bank_csv: Bank CSV file bytes
        internal_csv: Internal CSV file bytes
    
    Returns:
        Dict with keys: 'gateway', 'bank', 'internal'
        Each value is a list of normalized dicts with semantic field names.
    """
    mappings = config.get("mappings", {})
    
    # Parse each source
    gateway_mapping = mappings.get("payment_gateway", {})
    bank_mapping = mappings.get("bank", {})
    internal_mapping = mappings.get("internal", {})
    
    gateway_data = parse_csv(payment_gateway_csv, gateway_mapping)
    bank_data = parse_csv(bank_csv, bank_mapping)
    internal_data = parse_csv(internal_csv, internal_mapping)
    
    return {
        "gateway": gateway_data,
        "bank": bank_data,
        "internal": internal_data
    }


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date string to datetime object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _amount_within_tolerance(
    amount1: float,
    amount2: float,
    tolerance_pct: float
) -> bool:
    """Check if two amounts are within tolerance percentage."""
    if amount1 == 0 and amount2 == 0:
        return True
    if amount1 == 0 or amount2 == 0:
        return False
    
    diff = abs(amount1 - amount2)
    max_amount = max(abs(amount1), abs(amount2))
    tolerance = max_amount * (tolerance_pct / 100.0)
    
    return diff <= tolerance


def _dates_within_window(
    date1: Optional[datetime],
    date2: Optional[datetime],
    window_days: int
) -> bool:
    """Check if two dates are within the specified window."""
    if date1 is None or date2 is None:
        return False
    
    diff = abs((date1 - date2).days)
    return diff <= window_days


def reconcile_data(
    config: Dict,
    sources: Dict[str, List[Dict]]
) -> Dict:
    """
    Reconcile Reconciliation data by matching transactions across sources.
    
    Uses OPTIMAL (Hungarian) matching algorithm for globally optimal pairing,
    with split payment detection for many-to-one scenarios.
    
    Matching logic:
    1. Match gateway ↔ bank using optimal bipartite matching
    2. Detect split payments (multiple gateway → one bank)
    3. Match internal → existing gateway–bank groups
    
    Args:
        config: Configuration dict with:
               - amount_tolerance_pct: float (e.g., 0.5 for 0.5%)
               - date_window_days: int (e.g., 3)
               - enable_split_detection: bool (default: True)
        sources: Dict with 'gateway', 'bank', 'internal' lists
    
    Returns:
        Dict with:
        - groups: List of matched groups, each containing gateway, bank, internal items
        - exceptions: Dict with unmatched items by source
        - stats: Dict with reconciliation statistics including algorithm used
    """
    gateway = sources.get("gateway", [])
    bank = sources.get("bank", [])
    internal = sources.get("internal", [])
    
    tolerance_pct = config.get("amount_tolerance_pct", 0.5)
    window_days = config.get("date_window_days", 3)
    enable_splits = config.get("enable_split_detection", True)
    
    # Step 1: Use optimal matching for gateway ↔ bank
    gw_bank_result = OptimalReconciler.reconcile(
        sources=gateway,
        targets=bank,
        source_amount_key="net_amount",
        target_amount_key="amount",
        tolerance_pct=tolerance_pct,
        window_days=window_days,
        parse_date_fn=_parse_date,
        enable_splits=enable_splits
    )
    
    # Build groups from optimal matches
    groups = []
    matched_gateway_indices = set()
    matched_bank_indices = set()
    split_match_count = 0
    
    # 1:1 optimal matches
    for gw_idx, bank_idx in gw_bank_result["matches"]:
        matched_gateway_indices.add(gw_idx)
        matched_bank_indices.add(bank_idx)
        groups.append({
            "gateway": [gateway[gw_idx]],
            "bank": [bank[bank_idx]],
            "internal": [],
            "match_type": "optimal_1to1"
        })
    
    # Split payment matches (many gateway → one bank)
    for split in gw_bank_result["splits"]:
        matched_bank_indices.add(split.target_idx)
        gw_items = []
        for src_idx in split.source_indices:
            matched_gateway_indices.add(src_idx)
            gw_items.append(gateway[src_idx])
        
        groups.append({
            "gateway": gw_items,
            "bank": [bank[split.target_idx]],
            "internal": [],
            "match_type": "split_payment",
            "split_info": {
                "source_count": len(split.source_indices),
                "combined_amount": split.combined_amount,
                "target_amount": split.target_amount,
                "variance": split.variance,
                "variance_pct": split.variance_pct
            }
        })
        split_match_count += 1
    
    # Step 2: Match internal → existing gateway–bank groups
    matched_internal_indices = set()
    
    for i, int_item in enumerate(internal):
        if i in matched_internal_indices:
            continue
        
        int_amount = int_item.get("amount")
        int_date_str = int_item.get("date")
        int_date = _parse_date(int_date_str)
        
        if int_amount is None or int_date is None:
            continue
        
        # Try to find matching group
        best_group_idx = None
        best_group_score = None
        
        for group_idx, group in enumerate(groups):
            gw_items = group.get("gateway", [])
            bank_items = group.get("bank", [])
            
            if not gw_items or not bank_items:
                continue
            
            # Sum gateway amounts (for split payments)
            gw_amount = sum(item.get("net_amount", 0) or 0 for item in gw_items)
            gw_date = _parse_date(gw_items[0].get("date"))
            
            if gw_date is None:
                continue
            
            bank_amount = bank_items[0].get("amount") if bank_items else None
            
            # Check if internal matches bank amount
            matches_bank = _amount_within_tolerance(int_amount, bank_amount, tolerance_pct) if bank_amount else False
            
            # Check if internal (gross) is close to gateway net (accounting for fees)
            if int_amount >= gw_amount:
                fee_pct = ((int_amount - gw_amount) / int_amount * 100) if int_amount > 0 else 0
                matches_gw = fee_pct <= 10.0
            else:
                matches_gw = _amount_within_tolerance(int_amount, gw_amount, tolerance_pct)
            
            if not (matches_gw or matches_bank):
                continue
            
            # Check date window
            if not _dates_within_window(int_date, gw_date, window_days):
                continue
            
            # Calculate match score
            amount_diff = abs(int_amount - gw_amount) if matches_gw else abs(int_amount - bank_amount)
            date_diff = abs((int_date - gw_date).days)
            score = amount_diff + (date_diff * 1000)
            
            if best_group_score is None or score < best_group_score:
                best_group_idx = group_idx
                best_group_score = score
        
        if best_group_idx is not None:
            groups[best_group_idx]["internal"].append(int_item)
            matched_internal_indices.add(i)
    
    # Step 3: Collect exceptions (unmatched items)
    exceptions = {
        "gateway": [gateway[i] for i in range(len(gateway)) if i not in matched_gateway_indices],
        "bank": [bank[i] for i in range(len(bank)) if i not in matched_bank_indices],
        "internal": [internal[i] for i in range(len(internal)) if i not in matched_internal_indices]
    }
    
    # Step 4: Calculate statistics
    total_gateway = len(gateway)
    total_bank = len(bank)
    total_internal = len(internal)
    
    matched_gateway = len(matched_gateway_indices)
    matched_bank = len(matched_bank_indices)
    matched_internal = len(matched_internal_indices)
    
    groups_with_internal = sum(1 for g in groups if g["internal"])
    
    stats = {
        "total_gateway": total_gateway,
        "total_bank": total_bank,
        "total_internal": total_internal,
        "matched_gateway": matched_gateway,
        "matched_bank": matched_bank,
        "matched_internal": matched_internal,
        "total_groups": len(groups),
        "split_matches": split_match_count,
        "groups_with_internal": groups_with_internal,
        "unmatched_gateway": len(exceptions["gateway"]),
        "unmatched_bank": len(exceptions["bank"]),
        "unmatched_internal": len(exceptions["internal"]),
        "match_rate_gateway": (matched_gateway / total_gateway * 100) if total_gateway > 0 else 0,
        "match_rate_bank": (matched_bank / total_bank * 100) if total_bank > 0 else 0,
        "match_rate_internal": (matched_internal / total_internal * 100) if total_internal > 0 else 0,
        "algorithm": "hungarian_optimal",
        "split_detection_enabled": enable_splits
    }
    
    return {
        "groups": groups,
        "exceptions": exceptions,
        "stats": stats
    }


def build_outputs(
    period_start: str,
    period_end: str,
    recon_result: Dict
) -> Dict[str, List[Dict]]:
    """
    Build formatted output rows for Reconciliation reconciliation results.
    
    Matches product spec schema exactly:
    - summary: period_start, period_end, total_gateway_volume, total_bank_volume, 
              total_internal_volume, matched_volume, matched_pct, exception_count, run_timestamp
    - reconciled: group_id, gateway_tx_ids[], bank_tx_ids[], internal_tx_ids[], 
                  amount_gateway, amount_bank, amount_internal, date_gateway, 
                  date_bank, date_internal, status
    - exceptions: source, tx_ids[], amounts, dates, description, reason, 
                  llm_explanation, suggested_action
    
    Args:
        period_start: Period start date (YYYY-MM-DD)
        period_end: Period end date (YYYY-MM-DD)
        recon_result: Result from reconcile_data with groups, exceptions, stats
    
    Returns:
        Dict with:
        - "summary": List of summary rows (one row)
        - "reconciled": List of reconciled group rows
        - "exceptions": List of exception rows with LLM explanations
    """
    from datetime import datetime, timezone
    
    stats = recon_result.get("stats", {})
    groups = recon_result.get("groups", [])
    exceptions = recon_result.get("exceptions", {})
    
    # Calculate volumes per source
    total_gateway_volume = 0.0
    total_bank_volume = 0.0
    total_internal_volume = 0.0
    matched_volume = 0.0
    
    # Calculate from all gateway items
    for exc in exceptions.get("gateway", []):
        total_gateway_volume += abs(exc.get("net_amount", 0) or 0)
    
    # Calculate from all bank items
    for exc in exceptions.get("bank", []):
        total_bank_volume += abs(exc.get("amount", 0) or 0)
    
    # Calculate from all internal items
    for exc in exceptions.get("internal", []):
        total_internal_volume += abs(exc.get("amount", 0) or 0)
    
    # Calculate volumes from matched groups
    for group in groups:
        for gw_item in group.get("gateway", []):
            gw_amount = abs(gw_item.get("net_amount", 0) or 0)
            total_gateway_volume += gw_amount
            matched_volume += gw_amount
        
        for bank_item in group.get("bank", []):
            bank_amount = abs(bank_item.get("amount", 0) or 0)
            total_bank_volume += bank_amount
        
        for int_item in group.get("internal", []):
            int_amount = abs(int_item.get("amount", 0) or 0)
            total_internal_volume += int_amount
    
    # Calculate matched percentage (based on gateway volume)
    matched_pct = (matched_volume / total_gateway_volume * 100) if total_gateway_volume > 0 else 0
    
    # Build summary row matching spec exactly
    summary_rows = [{
        "period_start": period_start,
        "period_end": period_end,
        "total_gateway_volume": round(total_gateway_volume, 2),
        "total_bank_volume": round(total_bank_volume, 2),
        "total_internal_volume": round(total_internal_volume, 2),
        "matched_volume": round(matched_volume, 2),
        "matched_pct": round(matched_pct, 2),
        "exception_count": stats.get("unmatched_gateway", 0) + stats.get("unmatched_bank", 0) + stats.get("unmatched_internal", 0),
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "algorithm": stats.get("algorithm", "hungarian_optimal"),
        "split_matches_count": stats.get("split_matches", 0)
    }]
    
    # Build reconciled rows (one per group) - matching spec exactly
    reconciled_rows = []
    for idx, group in enumerate(groups, start=1):
        gw_items = group.get("gateway", [])
        bank_items = group.get("bank", [])
        internal_items = group.get("internal", [])
        
        # Collect all transaction IDs as arrays
        gateway_tx_ids = [item.get("txn_id", "") for item in gw_items if item.get("txn_id")]
        bank_tx_ids = [item.get("bank_txn_id", "") for item in bank_items if item.get("bank_txn_id")]
        internal_tx_ids = [item.get("internal_id", "") for item in internal_items if item.get("internal_id")]
        
        # Get primary transaction details for amounts/dates
        gw_item = gw_items[0] if gw_items else {}
        bank_item = bank_items[0] if bank_items else {}
        internal_item = internal_items[0] if internal_items else {}
        
        # Determine match status
        has_gateway = len(gw_items) > 0
        has_bank = len(bank_items) > 0
        has_internal = len(internal_items) > 0
        is_split = group.get("match_type") == "split_payment"
        
        if is_split and has_internal:
            status = "3-way-split-match"
        elif is_split:
            status = "2-way-split-match"
        elif has_gateway and has_bank and has_internal:
            status = "3-way-match"
        elif has_gateway and has_bank:
            status = "2-way-match-gateway-bank"
        elif has_gateway and has_internal:
            status = "2-way-match-gateway-internal"
        elif has_bank and has_internal:
            status = "2-way-match-bank-internal"
        else:
            status = "partial-match"
        
        # Calculate amounts (sum if multiple transactions)
        amount_gateway = sum(abs(item.get("net_amount", 0) or 0) for item in gw_items)
        amount_bank = sum(abs(item.get("amount", 0) or 0) for item in bank_items)
        amount_internal = sum(abs(item.get("amount", 0) or 0) for item in internal_items)
        
        # Generate reasoning for this match
        source_txn = gw_item or bank_item or internal_item
        matched_txns = [t for t in [bank_item, internal_item] if t]
        
        reasoning = ReconciliationExplainer.explain_match(
            source_txn=source_txn,
            matched_txns=matched_txns,
            match_scores={},
            config={"amount_tolerance_pct": 0.5, "date_window_days": 3}
        )
        
        row = {
            "group_id": idx,
            "gateway_tx_ids": gateway_tx_ids,
            "bank_tx_ids": bank_tx_ids,
            "internal_tx_ids": internal_tx_ids,
            "amount_gateway": round(amount_gateway, 2),
            "amount_bank": round(amount_bank, 2),
            "amount_internal": round(amount_internal, 2),
            "date_gateway": gw_item.get("date", ""),
            "date_bank": bank_item.get("date", ""),
            "date_internal": internal_item.get("date", ""),
            "status": status,
            "confidence": reasoning.confidence,
            "confidence_level": reasoning.confidence_level.value,
            "reasoning": reasoning.to_tree_format(),
            "reasoning_steps": [
                {"factor": s.factor, "observation": s.observation, "impact": s.impact}
                for s in reasoning.steps
            ]
        }
        
        # Add split payment details if applicable
        if group.get("match_type") == "split_payment":
            split_info = group.get("split_info", {})
            row["is_split_payment"] = True
            row["split_source_count"] = split_info.get("source_count", 0)
            row["split_combined_amount"] = round(split_info.get("combined_amount", 0), 2)
            row["split_variance"] = round(split_info.get("variance", 0), 2)
            row["split_variance_pct"] = round(split_info.get("variance_pct", 0), 2)
        else:
            row["is_split_payment"] = False
        
        reconciled_rows.append(row)
    
    # Build exception rows with LLM explanations - matching spec exactly
    exception_rows = []
    
    # Process gateway exceptions
    for exc in exceptions.get("gateway", []):
        facts = {
            "source": "gateway",
            "txn_id": exc.get("txn_id", ""),
            "net_amount": exc.get("net_amount", 0),
            "date": exc.get("date", ""),
            "status": exc.get("status", ""),
            "reason": "no_match" if exc.get("status") != "failed" else "transaction_failed"
        }
        
        explanation = generate_variance_explanation(facts)
        
        # Generate detailed reasoning
        reasoning = ReconciliationExplainer.explain_exception(
            txn=exc,
            exception_type="unmatched_gateway",
            details=facts
        )
        
        exception_rows.append({
            "source": "gateway",
            "tx_ids": [exc.get("txn_id", "")] if exc.get("txn_id") else [],
            "amounts": exc.get("net_amount", 0),
            "dates": exc.get("date", ""),
            "description": exc.get("description", "") or exc.get("status", ""),
            "reason": explanation.get("reason", "no_match"),
            "llm_explanation": explanation.get("llm_explanation", ""),
            "suggested_action": explanation.get("suggested_action", ""),
            "reasoning": reasoning.to_tree_format(),
            "reasoning_steps": [
                {"factor": s.factor, "observation": s.observation, "impact": s.impact}
                for s in reasoning.steps
            ]
        })
    
    # Process bank exceptions
    for exc in exceptions.get("bank", []):
        facts = {
            "source": "bank",
            "bank_txn_id": exc.get("bank_txn_id", ""),
            "amount": exc.get("amount", 0),
            "date": exc.get("date", ""),
            "reason": "no_match"
        }
        
        explanation = generate_variance_explanation(facts)
        
        # Generate detailed reasoning
        reasoning = ReconciliationExplainer.explain_exception(
            txn=exc,
            exception_type="unmatched_bank",
            details=facts
        )
        
        exception_rows.append({
            "source": "bank",
            "tx_ids": [exc.get("bank_txn_id", "")] if exc.get("bank_txn_id") else [],
            "amounts": exc.get("amount", 0),
            "dates": exc.get("date", ""),
            "description": exc.get("description", ""),
            "reason": explanation.get("reason", "no_match"),
            "llm_explanation": explanation.get("llm_explanation", ""),
            "suggested_action": explanation.get("suggested_action", ""),
            "reasoning": reasoning.to_tree_format(),
            "reasoning_steps": [
                {"factor": s.factor, "observation": s.observation, "impact": s.impact}
                for s in reasoning.steps
            ]
        })
    
    # Process internal exceptions
    for exc in exceptions.get("internal", []):
        facts = {
            "source": "internal",
            "internal_id": exc.get("internal_id", ""),
            "amount": exc.get("amount", 0),
            "date": exc.get("date", ""),
            "reason": "no_match"
        }
        
        explanation = generate_variance_explanation(facts)
        
        # Generate detailed reasoning
        reasoning = ReconciliationExplainer.explain_exception(
            txn=exc,
            exception_type="unmatched_internal",
            details=facts
        )
        
        exception_rows.append({
            "source": "internal",
            "tx_ids": [exc.get("internal_id", "")] if exc.get("internal_id") else [],
            "amounts": exc.get("amount", 0),
            "dates": exc.get("date", ""),
            "description": exc.get("description", ""),
            "reason": explanation.get("reason", "no_match"),
            "llm_explanation": explanation.get("llm_explanation", ""),
            "suggested_action": explanation.get("suggested_action", ""),
            "reasoning": reasoning.to_tree_format(),
            "reasoning_steps": [
                {"factor": s.factor, "observation": s.observation, "impact": s.impact}
                for s in reasoning.steps
            ]
        })
    
    # Detect potential duplicates across all sources
    all_transactions = []
    for group in groups:
        all_transactions.extend(group.get("gateway", []))
        all_transactions.extend(group.get("bank", []))
        all_transactions.extend(group.get("internal", []))
    
    duplicate_detector = DuplicateDetector()
    duplicates = duplicate_detector.find_duplicates(all_transactions)
    
    # Add duplicate warnings to summary
    if duplicates:
        summary_rows[0]["duplicate_warnings"] = len(duplicates)
        summary_rows[0]["duplicate_details"] = [
            {
                "count": d["count"],
                "total_amount": d["total_amount"],
                "reasons": d["reasons"]
            }
            for d in duplicates[:5]  # Limit to top 5
        ]
    
    return {
        "summary": summary_rows,
        "reconciled": reconciled_rows,
        "exceptions": exception_rows,
        "duplicates": duplicates if duplicates else None
    }


def send_summary_notification(
    config: Dict,
    period_start: str,
    period_end: str,
    outputs: Dict[str, List[Dict]]
) -> bool:
    """
    Send a summary notification via Slack and/or Teams APPS.
    
    Args:
        config: Configuration dict with channel preferences
        period_start: Period start date (YYYY-MM-DD)
        period_end: Period end date (YYYY-MM-DD)
        outputs: Output from build_outputs
    
    Returns:
        bool: True if at least one notification sent successfully, False otherwise
    """
    import os
    import asyncio
    
    # Try to use app-based notifications
    try:
        from ui.slack.app import send_slack_message, build_reconciliation_result_blocks
        SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
        SLACK_CHANNEL = config.get("slack_channel", "#finance")
    except ImportError:
        SLACK_BOT_TOKEN = None
    
    if not SLACK_BOT_TOKEN:
        return False
    
    # Build the result in the format expected by the app
    result = {
        "summary": outputs.get("summary", []),
        "reconciled": outputs.get("reconciled", []),
        "exceptions": outputs.get("exceptions", [])
    }
    
    blocks = build_reconciliation_result_blocks(result)
    
    # Add period info header
    blocks.insert(1, {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Period: {period_start} to {period_end}"}
        ]
    })
    
    success = False
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(
            send_slack_message(SLACK_CHANNEL, blocks, token=SLACK_BOT_TOKEN)
        )
        loop.close()
        success = response.get("ok", False)
    except Exception as e:
        print(f"Failed to send Slack app notification: {str(e)}")
    
    return success


def reconcile_with_multi_factor_scoring(
    config: Dict,
    sources: Dict[str, List[Dict]],
    use_pattern_learning: bool = True,
) -> Dict[str, Any]:
    """
    Reconcile data using the 100-point multi-factor scoring system.
    
    Per product_spec_updated.md:
    - Amount Match: 0-40 points
    - Date Proximity: 0-30 points
    - Description Similarity: 0-20 points
    - Reference Match: 0-10 points
    - Pattern boost: 0-20 points (from learned patterns)
    
    Thresholds:
    - Auto-match: 80+ points
    - Auto-draft JE: 90+ points
    
    Args:
        config: Configuration dict
        sources: Dict with 'gateway', 'bank', 'internal' transaction lists
        use_pattern_learning: Whether to apply learned patterns for boost
        
    Returns:
        Dict with groups, exceptions, stats, and scoring details
    """
    gateway = sources.get("gateway", [])
    bank = sources.get("bank", [])
    internal = sources.get("internal", [])
    
    # Initialize services
    pattern_service = PatternLearningService() if use_pattern_learning else None
    scorer = MultiFactorScorer(pattern_service=pattern_service)
    priority_classifier = ExceptionPriorityClassifier()
    
    # Step 1: Score all gateway-bank pairs
    scoring_result = scorer.optimal_matching(
        gateway, bank,
        source_amount_key="net_amount",
        target_amount_key="amount",
    )
    
    # Build matched groups with detailed scoring
    groups = []
    matched_gateway_indices = set()
    matched_bank_indices = set()
    auto_match_count = 0
    auto_je_count = 0
    
    for gw_idx, bank_idx, score in scoring_result["matches"]:
        matched_gateway_indices.add(gw_idx)
        matched_bank_indices.add(bank_idx)
        
        groups.append({
            "gateway": [gateway[gw_idx]],
            "bank": [bank[bank_idx]],
            "internal": [],
            "match_type": "multi_factor_match",
            "score": score.total_score,
            "score_breakdown": score.to_dict(),
            "confidence_level": score.confidence_level.value,
            "auto_match": score.total_score >= MultiFactorScorer.AUTO_MATCH_THRESHOLD,
            "auto_je": score.total_score >= MultiFactorScorer.AUTO_JE_THRESHOLD,
        })
        
        auto_match_count += 1
        if score.total_score >= MultiFactorScorer.AUTO_JE_THRESHOLD:
            auto_je_count += 1
    
    # Step 2: Match internal to existing groups
    matched_internal_indices = set()
    
    for i, int_item in enumerate(internal):
        int_amount = int_item.get("amount")
        if int_amount is None:
            continue
        
        best_group_idx = None
        best_score = 0.0
        
        for group_idx, group in enumerate(groups):
            gw_items = group.get("gateway", [])
            bank_items = group.get("bank", [])
            
            if not gw_items or not bank_items:
                continue
            
            # Score internal against gateway
            gw_item = gw_items[0]
            score = scorer.score_match(
                int_item, gw_item,
                source_amount_key="amount",
                target_amount_key="net_amount",
            )
            
            if score.total_score > best_score and score.total_score >= 60:
                best_score = score.total_score
                best_group_idx = group_idx
        
        if best_group_idx is not None:
            groups[best_group_idx]["internal"].append(int_item)
            matched_internal_indices.add(i)
    
    # Step 3: Build exceptions with priority classification
    raw_exceptions = []
    
    # Unmatched gateway
    for i, gw in enumerate(gateway):
        if i not in matched_gateway_indices:
            raw_exceptions.append({
                "source": "gateway",
                "tx_ids": [gw.get("txn_id", "")],
                "amount": gw.get("net_amount", 0),
                "amounts": gw.get("net_amount", 0),
                "dates": gw.get("date", ""),
                "description": gw.get("description", ""),
                "reason": "no_match",
            })
    
    # Unmatched bank
    for i, b in enumerate(bank):
        if i not in matched_bank_indices:
            raw_exceptions.append({
                "source": "bank",
                "tx_ids": [b.get("bank_txn_id", "")],
                "amount": b.get("amount", 0),
                "amounts": b.get("amount", 0),
                "dates": b.get("date", ""),
                "description": b.get("description", ""),
                "reason": "missing_counterparty",
            })
    
    # Unmatched internal
    for i, int_item in enumerate(internal):
        if i not in matched_internal_indices:
            raw_exceptions.append({
                "source": "internal",
                "tx_ids": [int_item.get("internal_id", "")],
                "amount": int_item.get("amount", 0),
                "amounts": int_item.get("amount", 0),
                "dates": int_item.get("date", ""),
                "description": int_item.get("description", ""),
                "reason": "no_match",
            })
    
    # Classify exceptions by priority
    classified_exceptions = classify_exceptions(raw_exceptions)
    
    # Build stats
    total_gateway = len(gateway)
    total_bank = len(bank)
    total_internal = len(internal)
    
    stats = {
        "total_gateway": total_gateway,
        "total_bank": total_bank,
        "total_internal": total_internal,
        "matched_gateway": len(matched_gateway_indices),
        "matched_bank": len(matched_bank_indices),
        "matched_internal": len(matched_internal_indices),
        "total_groups": len(groups),
        "auto_match_count": auto_match_count,
        "auto_je_count": auto_je_count,
        "unmatched_gateway": total_gateway - len(matched_gateway_indices),
        "unmatched_bank": total_bank - len(matched_bank_indices),
        "unmatched_internal": total_internal - len(matched_internal_indices),
        "match_rate_gateway": (len(matched_gateway_indices) / total_gateway * 100) if total_gateway > 0 else 0,
        "match_rate_bank": (len(matched_bank_indices) / total_bank * 100) if total_bank > 0 else 0,
        "algorithm": "multi_factor_scoring",
        "scoring_thresholds": {
            "auto_match": MultiFactorScorer.AUTO_MATCH_THRESHOLD,
            "auto_je": MultiFactorScorer.AUTO_JE_THRESHOLD,
        },
        "exception_summary": priority_classifier.generate_summary(classified_exceptions),
    }
    
    return {
        "groups": groups,
        "exceptions": [e.to_dict() for e in classified_exceptions],
        "stats": stats,
    }


def run_autonomous_reconciliation(
    config: Dict,
    sources: Dict[str, List[Dict]],
    period_start: str,
    period_end: str,
    auto_generate_je: bool = True,
) -> Dict[str, Any]:
    """
    Run full autonomous reconciliation as per product_spec_updated.md.
    
    Complete workflow:
    1. Multi-factor matching (100-point system)
    2. Pattern learning application
    3. Exception classification with priority
    4. Auto-generate draft JEs for 90%+ matches
    5. Build outputs for Sheets
    
    Args:
        config: Configuration dict
        sources: Dict with 'gateway', 'bank', 'internal' lists
        period_start: Period start date (YYYY-MM-DD)
        period_end: Period end date (YYYY-MM-DD)
        auto_generate_je: Whether to auto-generate journal entries
        
    Returns:
        Complete reconciliation result with groups, exceptions, drafts, outputs
    """
    # Step 1: Run multi-factor reconciliation
    recon_result = reconcile_with_multi_factor_scoring(
        config=config,
        sources=sources,
        use_pattern_learning=True,
    )
    
    # Step 2: Build outputs for Sheets
    outputs = build_outputs(period_start, period_end, recon_result)
    
    # Add confidence scores from groups
    for i, rec in enumerate(outputs.get("reconciled", [])):
        if i < len(recon_result["groups"]):
            group = recon_result["groups"][i]
            rec["confidence"] = group.get("score", 85.0)
            rec["confidence_level"] = group.get("confidence_level", "medium")
            rec["auto_je_eligible"] = group.get("auto_je", False)
    
    # Step 3: Auto-generate journal entries
    je_result = None
    if auto_generate_je:
        from clearledgr.services.journal_entries import JournalEntryService
        je_service = JournalEntryService()
        
        # Build input with groups and confidence
        je_input = {
            "groups": recon_result["groups"],
            "reconciled": outputs.get("reconciled", []),
        }
        
        je_result = je_service.auto_generate_from_reconciliation(je_input)
    
    # Step 4: Update stats
    stats = recon_result["stats"]
    if je_result:
        stats["draft_je_generated"] = je_result["generated_count"]
        stats["draft_je_skipped"] = je_result["skipped_count"]
        stats["draft_je_total_amount"] = je_result["total_amount"]
        stats["draft_je_avg_confidence"] = je_result["average_confidence"]
    
    return {
        "summary": outputs.get("summary", []),
        "reconciled": outputs.get("reconciled", []),
        "exceptions": outputs.get("exceptions", []),
        "groups": recon_result["groups"],
        "stats": stats,
        "draft_entries": [je.entry_id for je in je_result["entries"]] if je_result else [],
        "duplicates": outputs.get("duplicates"),
    }
