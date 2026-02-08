"""
Agent Data Quality Service for Clearledgr Reconciliation v1

Checks data quality before reconciliation.
"""
from typing import Dict, List, Optional
from datetime import datetime


def check_data_quality(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """
    Perform comprehensive data quality checks.
    
    Args:
        gateway_data: Gateway transaction data
        bank_data: Bank transaction data
        internal_data: Internal transaction data
    
    Returns:
        Dict with quality check results
    """
    issues = []
    warnings = []
    
    # Check completeness
    completeness = _check_completeness(gateway_data, bank_data, internal_data)
    issues.extend(completeness.get("issues", []))
    warnings.extend(completeness.get("warnings", []))
    
    # Check required fields
    required_fields = _check_required_fields(gateway_data, bank_data, internal_data)
    issues.extend(required_fields.get("issues", []))
    warnings.extend(required_fields.get("warnings", []))
    
    # Check date consistency
    date_consistency = _check_date_consistency(gateway_data, bank_data, internal_data)
    issues.extend(date_consistency.get("issues", []))
    warnings.extend(date_consistency.get("warnings", []))
    
    # Check amount consistency
    amount_consistency = _check_amount_consistency(gateway_data, bank_data, internal_data)
    issues.extend(amount_consistency.get("issues", []))
    warnings.extend(amount_consistency.get("warnings", []))
    
    # Check for duplicates
    duplicates = _check_duplicates(gateway_data, bank_data, internal_data)
    issues.extend(duplicates.get("issues", []))
    warnings.extend(duplicates.get("warnings", []))
    
    quality_score = _calculate_quality_score(issues, warnings)
    
    return {
        "quality_score": quality_score,
        "status": "good" if quality_score >= 0.8 else "warning" if quality_score >= 0.6 else "poor",
        "issues": issues,
        "warnings": warnings,
        "suggestions": _generate_quality_suggestions(issues, warnings)
    }


def _check_completeness(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """Check data completeness."""
    issues = []
    warnings = []
    
    if not gateway_data:
        issues.append({
            "source": "gateway",
            "type": "empty",
            "message": "Gateway data is empty"
        })
    elif len(gateway_data) < 10:
        warnings.append({
            "source": "gateway",
            "type": "low_volume",
            "message": f"Gateway data has only {len(gateway_data)} transactions"
        })
    
    if not bank_data:
        issues.append({
            "source": "bank",
            "type": "empty",
            "message": "Bank data is empty"
        })
    elif len(bank_data) < 10:
        warnings.append({
            "source": "bank",
            "type": "low_volume",
            "message": f"Bank data has only {len(bank_data)} transactions"
        })
    
    if not internal_data:
        warnings.append({
            "source": "internal",
            "type": "empty",
            "message": "Internal data is empty (3-way matching may not be possible)"
        })
    
    return {"issues": issues, "warnings": warnings}


def _check_required_fields(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """Check for required fields."""
    issues = []
    warnings = []
    
    required_gateway = ["transaction_id", "date", "net_amount"]
    required_bank = ["bank_tx_id", "bank_date", "bank_amount"]
    required_internal = ["internal_tx_id", "internal_date", "internal_amount"]
    
    for row in gateway_data:
        missing = [field for field in required_gateway if not row.get(field)]
        if missing:
            issues.append({
                "source": "gateway",
                "type": "missing_fields",
                "message": f"Missing required fields: {', '.join(missing)}",
                "row": row.get("transaction_id", "unknown")
            })
            break  # Report once per source
    
    for row in bank_data:
        missing = [field for field in required_bank if not row.get(field)]
        if missing:
            issues.append({
                "source": "bank",
                "type": "missing_fields",
                "message": f"Missing required fields: {', '.join(missing)}",
                "row": row.get("bank_tx_id", "unknown")
            })
            break
    
    for row in internal_data:
        missing = [field for field in required_internal if not row.get(field)]
        if missing:
            issues.append({
                "source": "internal",
                "type": "missing_fields",
                "message": f"Missing required fields: {', '.join(missing)}",
                "row": row.get("internal_tx_id", "unknown")
            })
            break
    
    return {"issues": issues, "warnings": warnings}


def _check_date_consistency(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """Check date consistency."""
    issues = []
    warnings = []
    
    # Check for dates far in future/past
    today = datetime.now()
    
    for row in gateway_data:
        date_str = row.get("date")
        if date_str:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                days_diff = (date - today).days
                if days_diff > 365:
                    warnings.append({
                        "source": "gateway",
                        "type": "future_date",
                        "message": f"Date {date_str} is more than 1 year in the future"
                    })
                elif days_diff < -365:
                    warnings.append({
                        "source": "gateway",
                        "type": "past_date",
                        "message": f"Date {date_str} is more than 1 year in the past"
                    })
            except (ValueError, TypeError):
                issues.append({
                    "source": "gateway",
                    "type": "invalid_date",
                    "message": f"Invalid date format: {date_str}"
                })
    
    return {"issues": issues, "warnings": warnings}


def _check_amount_consistency(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """Check amount consistency."""
    issues = []
    warnings = []
    
    # Check for zero amounts
    zero_gateway = sum(1 for row in gateway_data if row.get("net_amount", 0) == 0)
    if zero_gateway > len(gateway_data) * 0.1:  # >10% zeros
        warnings.append({
            "source": "gateway",
            "type": "zero_amounts",
            "message": f"{zero_gateway} transactions have zero amount"
        })
    
    # Check for extremely large amounts (potential data errors)
    gateway_amounts = [abs(row.get("net_amount", 0)) for row in gateway_data if row.get("net_amount")]
    if gateway_amounts:
        max_amount = max(gateway_amounts)
        avg_amount = sum(gateway_amounts) / len(gateway_amounts)
        if max_amount > avg_amount * 100:  # >100x average
            warnings.append({
                "source": "gateway",
                "type": "outlier_amount",
                "message": f"Extremely large amount detected: {max_amount:.2f} (avg: {avg_amount:.2f})"
            })
    
    return {"issues": issues, "warnings": warnings}


def _check_duplicates(
    gateway_data: List[Dict],
    bank_data: List[Dict],
    internal_data: List[Dict]
) -> Dict:
    """Check for duplicate transactions."""
    issues = []
    warnings = []
    
    # Check gateway duplicates
    gateway_ids = [row.get("transaction_id") for row in gateway_data if row.get("transaction_id")]
    if len(gateway_ids) != len(set(gateway_ids)):
        duplicates = len(gateway_ids) - len(set(gateway_ids))
        warnings.append({
            "source": "gateway",
            "type": "duplicates",
            "message": f"{duplicates} duplicate transaction IDs found"
        })
    
    # Check bank duplicates
    bank_ids = [row.get("bank_tx_id") for row in bank_data if row.get("bank_tx_id")]
    if len(bank_ids) != len(set(bank_ids)):
        duplicates = len(bank_ids) - len(set(bank_ids))
        warnings.append({
            "source": "bank",
            "type": "duplicates",
            "message": f"{duplicates} duplicate transaction IDs found"
        })
    
    return {"issues": issues, "warnings": warnings}


def _calculate_quality_score(issues: List[Dict], warnings: List[Dict]) -> float:
    """Calculate overall quality score (0.0 to 1.0)."""
    # Start with perfect score
    score = 1.0
    
    # Deduct for issues (more severe)
    score -= len(issues) * 0.2
    
    # Deduct for warnings (less severe)
    score -= len(warnings) * 0.1
    
    return max(0.0, min(1.0, score))


def _generate_quality_suggestions(issues: List[Dict], warnings: List[Dict]) -> List[str]:
    """Generate suggestions based on quality issues."""
    suggestions = []
    
    if any(issue["type"] == "empty" for issue in issues):
        suggestions.append("One or more data sources are empty. Verify data sources and try again.")
    
    if any(issue["type"] == "missing_fields" for issue in issues):
        suggestions.append("Some required fields are missing. Check column mappings and data format.")
    
    if any(warning["type"] == "duplicates" for warning in warnings):
        suggestions.append("Duplicate transactions detected. Review data sources for duplicates.")
    
    if any(warning["type"] == "zero_amounts" for warning in warnings):
        suggestions.append("Many zero-amount transactions found. Verify data completeness.")
    
    if not suggestions:
        suggestions.append("Data quality looks good. Proceed with reconciliation.")
    
    return suggestions

