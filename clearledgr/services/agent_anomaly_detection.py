"""
Agent Anomaly Detection Service for Clearledgr Reconciliation v1

Detects anomalies and unusual patterns in financial data.
"""
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from statistics import mean, stdev


def detect_volume_anomalies(
    current_volume: float,
    historical_volumes: List[float],
    threshold_std: float = 2.0
) -> Dict:
    """
    Detect volume anomalies (sudden spikes or drops).
    
    Args:
        current_volume: Current period volume
        historical_volumes: List of historical volumes
        threshold_std: Number of standard deviations for threshold
    
    Returns:
        Dict with anomaly detection results
    """
    if len(historical_volumes) < 3:
        return {
            "is_anomaly": False,
            "reason": "insufficient_history",
            "confidence": 0.0
        }
    
    avg_volume = mean(historical_volumes)
    volume_std = stdev(historical_volumes) if len(historical_volumes) > 1 else 0
    
    if volume_std == 0:
        return {
            "is_anomaly": False,
            "reason": "no_variance",
            "confidence": 0.0
        }
    
    z_score = (current_volume - avg_volume) / volume_std if volume_std > 0 else 0
    
    is_anomaly = abs(z_score) > threshold_std
    anomaly_type = None
    
    if is_anomaly:
        if z_score > threshold_std:
            anomaly_type = "spike"
        elif z_score < -threshold_std:
            anomaly_type = "drop"
    
    confidence = min(1.0, abs(z_score) / threshold_std) if is_anomaly else 0.0
    
    return {
        "is_anomaly": is_anomaly,
        "anomaly_type": anomaly_type,
        "z_score": z_score,
        "current_volume": current_volume,
        "average_volume": avg_volume,
        "confidence": confidence,
        "suggestion": _get_volume_anomaly_suggestion(anomaly_type, z_score) if is_anomaly else None
    }


def detect_match_rate_anomalies(
    current_match_rate: float,
    historical_match_rates: List[float],
    threshold_std: float = 2.0
) -> Dict:
    """
    Detect anomalies in match rates.
    
    Args:
        current_match_rate: Current match rate percentage
        historical_match_rates: List of historical match rates
        threshold_std: Number of standard deviations for threshold
    
    Returns:
        Dict with anomaly detection results
    """
    if len(historical_match_rates) < 3:
        return {
            "is_anomaly": False,
            "reason": "insufficient_history",
            "confidence": 0.0
        }
    
    avg_rate = mean(historical_match_rates)
    rate_std = stdev(historical_match_rates) if len(historical_match_rates) > 1 else 0
    
    if rate_std == 0:
        return {
            "is_anomaly": False,
            "reason": "no_variance",
            "confidence": 0.0
        }
    
    z_score = (current_match_rate - avg_rate) / rate_std if rate_std > 0 else 0
    
    is_anomaly = abs(z_score) > threshold_std
    anomaly_type = None
    
    if is_anomaly:
        if z_score < -threshold_std:  # Lower match rate is bad
            anomaly_type = "degradation"
        elif z_score > threshold_std:  # Higher match rate is good, but unusual
            anomaly_type = "improvement"
    
    confidence = min(1.0, abs(z_score) / threshold_std) if is_anomaly else 0.0
    
    return {
        "is_anomaly": is_anomaly,
        "anomaly_type": anomaly_type,
        "z_score": z_score,
        "current_match_rate": current_match_rate,
        "average_match_rate": avg_rate,
        "confidence": confidence,
        "suggestion": _get_match_rate_anomaly_suggestion(anomaly_type, z_score) if is_anomaly else None
    }


def detect_exception_patterns(
    exceptions: List[Dict],
    historical_exceptions: List[List[Dict]]
) -> Dict:
    """
    Detect patterns in exceptions.
    
    Args:
        exceptions: Current period exceptions
        historical_exceptions: List of historical exception lists
    
    Returns:
        Dict with pattern detection results
    """
    current_count = len(exceptions)
    
    if len(historical_exceptions) < 2:
        return {
            "has_pattern": False,
            "reason": "insufficient_history"
        }
    
    historical_counts = [len(exc_list) for exc_list in historical_exceptions]
    avg_count = mean(historical_counts)
    
    # Check for sudden increase
    if current_count > avg_count * 1.5:
        return {
            "has_pattern": True,
            "pattern_type": "exception_spike",
            "current_count": current_count,
            "average_count": avg_count,
            "suggestion": "Review exceptions - significant increase detected. Consider checking data quality or matching configuration."
        }
    
    # Check for common exception reasons
    if exceptions:
        reason_counts = {}
        for exc in exceptions:
            reason = exc.get("reason", "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        most_common_reason = max(reason_counts.items(), key=lambda x: x[1])
        if most_common_reason[1] > len(exceptions) * 0.5:  # >50% same reason
            return {
                "has_pattern": True,
                "pattern_type": "common_reason",
                "reason": most_common_reason[0],
                "count": most_common_reason[1],
                "percentage": (most_common_reason[1] / len(exceptions)) * 100,
                "suggestion": f"Most exceptions ({most_common_reason[1]}/{len(exceptions)}) are due to: {most_common_reason[0]}. Consider adjusting matching configuration."
            }
    
    return {
        "has_pattern": False,
        "reason": "no_significant_patterns"
    }


def _get_volume_anomaly_suggestion(anomaly_type: str, z_score: float) -> str:
    """Get suggestion for volume anomaly."""
    if anomaly_type == "spike":
        return f"Volume spike detected (z-score: {z_score:.2f}). Verify data completeness and check for duplicate transactions."
    elif anomaly_type == "drop":
        return f"Volume drop detected (z-score: {z_score:.2f}). Verify all data sources are included and check for missing periods."
    return "Volume anomaly detected. Review data sources."


def _get_match_rate_anomaly_suggestion(anomaly_type: str, z_score: float) -> str:
    """Get suggestion for match rate anomaly."""
    if anomaly_type == "degradation":
        return f"Match rate degradation detected (z-score: {z_score:.2f}). Consider reviewing matching tolerances or data quality."
    elif anomaly_type == "improvement":
        return f"Match rate improvement detected (z-score: {z_score:.2f}). This is positive - consider documenting what changed."
    return "Match rate anomaly detected. Review reconciliation configuration."

