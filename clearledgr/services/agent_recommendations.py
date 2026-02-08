"""
Agent Recommendations Service for Clearledgr v1

Provides proactive recommendations for optimal reconciliation configuration.
"""
from typing import Dict, List, Optional
from clearledgr.services.agent_anomaly_detection import (
    detect_volume_anomalies, detect_match_rate_anomalies, detect_exception_patterns
)
from clearledgr.services.agent_quality import check_data_quality
from clearledgr.services.agent_learning import get_learned_config


def list_reconciliation_runs(limit: int = 10, **kwargs) -> List[Dict]:
    """Stub: List reconciliation runs. Returns empty list for now."""
    return []


def get_reconciliation_stats(**kwargs) -> Dict:
    """Stub: Get reconciliation stats. Returns empty stats for now."""
    return {"total_runs": 0, "avg_match_rate": 0}


def get_proactive_recommendations(
    organization_id: str,
    tool_type: str,
    current_data: Optional[Dict] = None,
    historical_runs: Optional[List[Dict]] = None
) -> List[Dict]:
    """
    Get proactive recommendations for reconciliation.
    
    Args:
        organization_id: Organization ID
        tool_type: Tool type
        current_data: Current data to analyze (optional)
        historical_runs: Historical run data (optional)
    
    Returns:
        List of recommendation dicts
    """
    recommendations = []
    
    # Get learned configuration
    learned_config = get_learned_config(
        organization_id=organization_id,
        tool_type=tool_type,
        default_config={}
    )
    
    if learned_config:
        recommendations.append({
            "type": "learned_config",
            "priority": "high",
            "title": "Use Learned Configuration",
            "message": "Agent has learned optimal settings from your previous reconciliations.",
            "config": learned_config,
            "action": "apply_learned_config"
        })
    
    # Analyze historical data if available
    if historical_runs:
        historical_analysis = _analyze_historical_runs(historical_runs)
        recommendations.extend(historical_analysis)
    
    # Analyze current data if available
    if current_data:
        current_analysis = _analyze_current_data(current_data, historical_runs)
        recommendations.extend(current_analysis)
    
    # General recommendations
    general_recommendations = _get_general_recommendations(organization_id, tool_type)
    recommendations.extend(general_recommendations)
    
    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 2))
    
    return recommendations


def _analyze_historical_runs(historical_runs: List[Dict]) -> List[Dict]:
    """Analyze historical runs for patterns."""
    recommendations = []
    
    if len(historical_runs) < 3:
        return recommendations
    
    # Extract match rates
    match_rates = []
    volumes = []
    exception_counts = []
    
    for run in historical_runs:
        summary = run.get("summary", [{}])[0] if run.get("summary") else {}
        match_rates.append(summary.get("matched_pct", 0))
        volumes.append(summary.get("total_gateway_volume", 0))
        exception_counts.append(len(run.get("exceptions", [])))
    
    # Check for match rate anomalies
    if len(match_rates) >= 3:
        latest_rate = match_rates[-1]
        historical_rates = match_rates[:-1]
        
        anomaly = detect_match_rate_anomalies(latest_rate, historical_rates)
        if anomaly.get("is_anomaly"):
            recommendations.append({
                "type": "match_rate_anomaly",
                "priority": "high" if anomaly["anomaly_type"] == "degradation" else "medium",
                "title": "Match Rate Anomaly Detected",
                "message": anomaly.get("suggestion", "Match rate anomaly detected."),
                "anomaly": anomaly,
                "action": "review_matching_config"
            })
    
    # Check for volume anomalies
    if len(volumes) >= 3:
        latest_volume = volumes[-1]
        historical_volumes = volumes[:-1]
        
        anomaly = detect_volume_anomalies(latest_volume, historical_volumes)
        if anomaly.get("is_anomaly"):
            recommendations.append({
                "type": "volume_anomaly",
                "priority": "medium",
                "title": "Volume Anomaly Detected",
                "message": anomaly.get("suggestion", "Volume anomaly detected."),
                "anomaly": anomaly,
                "action": "verify_data_completeness"
            })
    
    return recommendations


def _analyze_current_data(
    current_data: Dict,
    historical_runs: Optional[List[Dict]] = None
) -> List[Dict]:
    """Analyze current data for recommendations."""
    recommendations = []
    
    # Check data quality
    gateway_data = current_data.get("gateway", [])
    bank_data = current_data.get("bank", [])
    internal_data = current_data.get("internal", [])
    
    quality_check = check_data_quality(gateway_data, bank_data, internal_data)
    
    if quality_check["status"] != "good":
        recommendations.append({
            "type": "data_quality",
            "priority": "high" if quality_check["status"] == "poor" else "medium",
            "title": "Data Quality Issues Detected",
            "message": f"Data quality score: {quality_check['quality_score']:.2f}",
            "issues": quality_check["issues"],
            "warnings": quality_check["warnings"],
            "suggestions": quality_check["suggestions"],
            "action": "fix_data_quality"
        })
    
    # Suggest optimal tolerance based on data spread
    if gateway_data and bank_data:
        tolerance_suggestion = _suggest_optimal_tolerance(gateway_data, bank_data)
        if tolerance_suggestion:
            recommendations.append(tolerance_suggestion)
    
    return recommendations


def _suggest_optimal_tolerance(
    gateway_data: List[Dict],
    bank_data: List[Dict]
) -> Optional[Dict]:
    """Suggest optimal tolerance based on data characteristics."""
    gateway_amounts = [abs(row.get("net_amount", 0)) for row in gateway_data if row.get("net_amount")]
    bank_amounts = [abs(row.get("bank_amount", 0)) for row in bank_data if row.get("bank_amount")]
    
    if not gateway_amounts or not bank_amounts:
        return None
    
    avg_gateway = sum(gateway_amounts) / len(gateway_amounts)
    avg_bank = sum(bank_amounts) / len(bank_amounts)
    
    # If amounts are very similar, suggest lower tolerance
    # If amounts vary significantly, suggest higher tolerance
    amount_variance = abs(avg_gateway - avg_bank) / max(avg_gateway, avg_bank) if max(avg_gateway, avg_bank) > 0 else 0
    
    if amount_variance < 0.01:  # Very similar
        suggested_tolerance = 0.1
    elif amount_variance < 0.05:  # Similar
        suggested_tolerance = 0.5
    else:  # Different
        suggested_tolerance = 1.0
    
    return {
        "type": "tolerance_suggestion",
        "priority": "low",
        "title": "Optimal Tolerance Suggestion",
        "message": f"Based on data characteristics, suggested tolerance: {suggested_tolerance}%",
        "suggested_tolerance": suggested_tolerance,
        "reasoning": f"Amount variance: {amount_variance:.2%}",
        "action": "apply_tolerance"
    }


def _get_general_recommendations(
    organization_id: str,
    tool_type: str
) -> List[Dict]:
    """Get general recommendations."""
    recommendations = []
    
    # Check if scheduling is set up
    from clearledgr.state.agent_memory import get_agent_schedules
    schedules = get_agent_schedules(tool_type, organization_id)
    
    if not schedules:
        recommendations.append({
            "type": "setup_scheduling",
            "priority": "medium",
            "title": "Set Up Automated Scheduling",
            "message": "Enable automatic reconciliation runs to save time.",
            "action": "setup_schedule"
        })
    
    return recommendations


def recommend_reconciliation_frequency(
    historical_runs: List[Dict]
) -> str:
    """
    Recommend reconciliation frequency based on historical patterns.
    
    Args:
        historical_runs: List of historical runs
    
    Returns:
        Recommended frequency: 'daily', 'weekly', 'monthly'
    """
    if len(historical_runs) < 3:
        return "monthly"  # Default
    
    # Calculate average time between runs
    run_dates = []
    for run in historical_runs:
        started_at = run.get("started_at")
        if started_at:
            try:
                from datetime import datetime
                date = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                run_dates.append(date)
            except (ValueError, AttributeError):
                pass
    
    if len(run_dates) < 2:
        return "monthly"
    
    run_dates.sort()
    intervals = []
    for i in range(1, len(run_dates)):
        delta = (run_dates[i] - run_dates[i-1]).days
        intervals.append(delta)
    
    avg_interval = sum(intervals) / len(intervals) if intervals else 30
    
    if avg_interval <= 2:
        return "daily"
    elif avg_interval <= 10:
        return "weekly"
    else:
        return "monthly"

