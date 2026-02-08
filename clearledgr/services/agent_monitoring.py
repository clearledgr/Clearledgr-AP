"""
Agent Monitoring Service for Clearledgr Reconciliation v1

Monitors data changes and triggers autonomous agent execution.
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import hashlib


def detect_data_changes(
    current_data: List[Dict],
    previous_hash: Optional[str] = None
) -> Tuple[bool, str, Dict]:
    """
    Detect if data has changed since last check.
    
    Args:
        current_data: Current data rows
        previous_hash: Hash of previous data (if available)
    
    Returns:
        Tuple of (has_changed, new_hash, change_stats)
    """
    # Create hash of current data
    data_str = str(sorted([str(row) for row in current_data]))
    current_hash = hashlib.md5(data_str.encode()).hexdigest()
    
    has_changed = previous_hash is None or current_hash != previous_hash
    
    change_stats = {
        "row_count": len(current_data),
        "previous_hash": previous_hash,
        "current_hash": current_hash,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if has_changed and previous_hash is not None:
        # Calculate approximate change (simple heuristic)
        change_stats["estimated_new_rows"] = max(0, len(current_data) - (change_stats.get("previous_row_count", 0)))
    
    return has_changed, current_hash, change_stats


def detect_period_end(current_date: Optional[datetime] = None) -> Dict[str, bool]:
    """
    Detect if we're at a period end (month, quarter, year).
    
    Args:
        current_date: Date to check (defaults to today)
    
    Returns:
        Dict with period_end flags
    """
    if current_date is None:
        current_date = datetime.now()
    
    # Check if today is last day of month
    next_month = current_date.replace(day=28) + timedelta(days=4)
    is_month_end = (next_month - timedelta(days=next_month.day)).day == current_date.day
    
    # Check if today is last day of quarter
    quarter_end_months = [3, 6, 9, 12]
    is_quarter_end = is_month_end and current_date.month in quarter_end_months
    
    # Check if today is last day of year
    is_year_end = is_month_end and current_date.month == 12
    
    return {
        "is_month_end": is_month_end,
        "is_quarter_end": is_quarter_end,
        "is_year_end": is_year_end,
        "date": current_date.strftime("%Y-%m-%d")
    }


def should_trigger_reconciliation(
    last_run_date: Optional[str],
    schedule_type: str,
    data_changed: bool = False,
    period_end_detected: bool = False,
    threshold_met: bool = False
) -> bool:
    """
    Determine if reconciliation should be triggered automatically.
    
    Args:
        last_run_date: ISO date string of last run (YYYY-MM-DD)
        schedule_type: 'daily', 'weekly', 'monthly', 'on_change', 'period_end', 'threshold'
        data_changed: Whether data has changed
        period_end_detected: Whether period end was detected
        threshold_met: Whether data threshold was met
    
    Returns:
        True if reconciliation should be triggered
    """
    if schedule_type == "on_change":
        return data_changed
    
    if schedule_type == "period_end":
        return period_end_detected
    
    if schedule_type == "threshold":
        return threshold_met
    
    if not last_run_date:
        # First run - trigger if schedule is time-based
        return schedule_type in ["daily", "weekly", "monthly"]
    
    try:
        last_run = datetime.strptime(last_run_date, "%Y-%m-%d")
        now = datetime.now()
        days_since = (now - last_run).days
        
        if schedule_type == "daily":
            return days_since >= 1
        elif schedule_type == "weekly":
            return days_since >= 7
        elif schedule_type == "monthly":
            return days_since >= 30 or (now.month != last_run.month)
        
    except (ValueError, TypeError):
        # Invalid date - trigger to be safe
        return True
    
    return False


def check_data_threshold(
    current_count: int,
    threshold: int,
    previous_count: Optional[int] = None
) -> Tuple[bool, Dict]:
    """
    Check if data threshold has been met.
    
    Args:
        current_count: Current number of rows/transactions
        threshold: Threshold to check against
        previous_count: Previous count (for delta checks)
    
    Returns:
        Tuple of (threshold_met, stats)
    """
    stats = {
        "current_count": current_count,
        "threshold": threshold,
        "threshold_met": current_count >= threshold
    }
    
    if previous_count is not None:
        stats["delta"] = current_count - previous_count
        stats["delta_threshold_met"] = abs(stats["delta"]) >= threshold
    
    return stats["threshold_met"], stats


def get_suggested_period(
    current_date: Optional[datetime] = None,
    period_type: str = "monthly"
) -> Dict[str, str]:
    """
    Get suggested period dates based on current date and period type.
    
    Args:
        current_date: Date to base period on (defaults to today)
        period_type: 'monthly', 'quarterly', 'yearly'
    
    Returns:
        Dict with period_start and period_end
    """
    if current_date is None:
        current_date = datetime.now()
    
    if period_type == "monthly":
        period_start = current_date.replace(day=1)
        # Last day of month
        if current_date.month == 12:
            period_end = current_date.replace(day=31)
        else:
            next_month = current_date.replace(day=28) + timedelta(days=4)
            period_end = (next_month - timedelta(days=next_month.day)).replace(day=1) - timedelta(days=1)
    
    elif period_type == "quarterly":
        quarter = (current_date.month - 1) // 3
        period_start = current_date.replace(month=quarter * 3 + 1, day=1)
        # Last day of quarter
        if quarter == 3:
            period_end = current_date.replace(month=12, day=31)
        else:
            period_end = current_date.replace(month=(quarter + 1) * 3, day=1) - timedelta(days=1)
    
    elif period_type == "yearly":
        period_start = current_date.replace(month=1, day=1)
        period_end = current_date.replace(month=12, day=31)
    
    else:
        # Default to monthly
        period_start = current_date.replace(day=1)
        if current_date.month == 12:
            period_end = current_date.replace(day=31)
        else:
            next_month = current_date.replace(day=28) + timedelta(days=4)
            period_end = (next_month - timedelta(days=next_month.day)).replace(day=1) - timedelta(days=1)
    
    return {
        "period_start": period_start.strftime("%Y-%m-%d"),
        "period_end": period_end.strftime("%Y-%m-%d"),
        "period_type": period_type
    }

