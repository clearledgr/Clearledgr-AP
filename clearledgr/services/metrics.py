"""
Metrics collection for Clearledgr Reconciliation API.
"""
import time
from typing import Dict, Any
from collections import defaultdict
from datetime import datetime, timezone
import os

# In-memory metrics store (use Prometheus/StatsD in production)
_metrics: Dict[str, Any] = {
    "requests": defaultdict(int),
    "errors": defaultdict(int),
    "reconciliation_runs": defaultdict(int),
    "response_times": [],
    "start_time": datetime.now(timezone.utc).isoformat(),
}


def record_request(method: str, path: str, status_code: int, duration_ms: float):
    """Record HTTP request metrics."""
    _metrics["requests"][f"{method} {path}"] += 1
    _metrics["requests"][f"status_{status_code}"] += 1
    
    # Keep last 1000 response times
    _metrics["response_times"].append(duration_ms)
    if len(_metrics["response_times"]) > 1000:
        _metrics["response_times"] = _metrics["response_times"][-1000:]


def record_error(error_type: str, path: str = ""):
    """Record error metrics."""
    _metrics["errors"][error_type] += 1
    if path:
        _metrics["errors"][f"{error_type}:{path}"] += 1


def record_reconciliation_run(source_type: str, status: str, duration_ms: float):
    """Record reconciliation run metrics."""
    _metrics["reconciliation_runs"][f"{source_type}:{status}"] += 1


def get_metrics() -> Dict[str, Any]:
    """Get current metrics."""
    response_times = _metrics["response_times"]
    avg_response_time = sum(response_times) / len(response_times) if response_times else 0
    p95_response_time = sorted(response_times)[int(len(response_times) * 0.95)] if len(response_times) >= 20 else 0
    p99_response_time = sorted(response_times)[int(len(response_times) * 0.99)] if len(response_times) >= 100 else 0
    
    total_requests = sum(_metrics["requests"].values())
    total_errors = sum(_metrics["errors"].values())
    total_runs = sum(_metrics["reconciliation_runs"].values())
    
    uptime_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(_metrics["start_time"])).total_seconds()
    
    return {
        "uptime_seconds": int(uptime_seconds),
        "uptime_human": _format_uptime(uptime_seconds),
        "requests": {
            "total": total_requests,
            "by_endpoint": dict(_metrics["requests"]),
            "by_status": {k: v for k, v in _metrics["requests"].items() if k.startswith("status_")},
        },
        "errors": {
            "total": total_errors,
            "by_type": dict(_metrics["errors"]),
        },
        "reconciliation_runs": {
            "total": total_runs,
            "by_type_and_status": dict(_metrics["reconciliation_runs"]),
        },
        "performance": {
            "avg_response_time_ms": round(avg_response_time, 2),
            "p95_response_time_ms": round(p95_response_time, 2),
            "p99_response_time_ms": round(p99_response_time, 2),
            "requests_per_second": round(total_requests / uptime_seconds, 2) if uptime_seconds > 0 else 0,
        },
    }


def _format_uptime(seconds: float) -> str:
    """Format uptime in human-readable format."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)


def reset_metrics():
    """Reset all metrics (for testing)."""
    global _metrics
    _metrics = {
        "requests": defaultdict(int),
        "errors": defaultdict(int),
        "reconciliation_runs": defaultdict(int),
        "response_times": [],
        "start_time": datetime.now(timezone.utc).isoformat(),
    }

