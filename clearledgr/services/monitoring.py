"""
Clearledgr Monitoring & Alerting Service

Provides:
- Error tracking and aggregation
- Performance metrics
- Health checks
- Slack/email alerting
- Structured logging

In production, integrate with:
- Sentry for error tracking
- Prometheus/Grafana for metrics
- PagerDuty for on-call alerting
"""

import os
import json
import time
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Callable
from functools import wraps
from dataclasses import dataclass, field
from collections import defaultdict
import asyncio
import httpx

# Configuration
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
ALERT_SLACK_WEBHOOK = os.getenv("ALERT_SLACK_WEBHOOK", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("clearledgr")


@dataclass
class ErrorEvent:
    """Represents a tracked error."""
    error_type: str
    message: str
    traceback: str
    context: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    service: str = "clearledgr"
    severity: str = "error"  # error, warning, critical
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": self.message,
            "traceback": self.traceback,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "service": self.service,
            "severity": self.severity,
        }


@dataclass
class MetricPoint:
    """Represents a metric data point."""
    name: str
    value: float
    tags: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


class MonitoringService:
    """
    Central monitoring service for Clearledgr.
    
    Usage:
        monitor = get_monitor()
        
        # Track errors
        monitor.capture_error(exception, context={"user_id": "123"})
        
        # Record metrics
        monitor.record_metric("reconciliation.matches", 150, {"org": "acme"})
        
        # Track timing
        with monitor.timer("api.request"):
            await process_request()
        
        # Health checks
        status = await monitor.check_health()
    """
    
    def __init__(self):
        self._errors: List[ErrorEvent] = []
        self._metrics: List[MetricPoint] = []
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._alert_cooldown: Dict[str, datetime] = {}
        self._sentry_client = None
        
        # Initialize Sentry if configured
        if SENTRY_DSN:
            try:
                import sentry_sdk
                sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)
                self._sentry_client = sentry_sdk
                logger.info("Sentry initialized")
            except ImportError:
                logger.warning("Sentry SDK not installed")
    
    # ==================== ERROR TRACKING ====================
    
    def capture_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        severity: str = "error",
        alert: bool = True
    ) -> str:
        """
        Capture and track an error.
        
        Args:
            error: The exception to capture
            context: Additional context (user_id, request_id, etc.)
            severity: error, warning, or critical
            alert: Whether to send an alert
        
        Returns:
            Error ID for reference
        """
        error_event = ErrorEvent(
            error_type=type(error).__name__,
            message=str(error),
            traceback=traceback.format_exc(),
            context=context or {},
            severity=severity,
        )
        
        # Store locally
        self._errors.append(error_event)
        self._error_counts[error_event.error_type] += 1
        
        # Keep only last 1000 errors in memory
        if len(self._errors) > 1000:
            self._errors = self._errors[-1000:]
        
        # Log
        log_method = getattr(logger, severity, logger.error)
        log_method(
            f"{error_event.error_type}: {error_event.message}",
            extra={"context": context}
        )
        
        # Send to Sentry
        if self._sentry_client:
            with self._sentry_client.push_scope() as scope:
                for key, value in (context or {}).items():
                    scope.set_extra(key, value)
                self._sentry_client.capture_exception(error)
        
        # Alert if critical or high error count
        if alert and (severity == "critical" or self._should_alert(error_event.error_type)):
            asyncio.create_task(self._send_alert(error_event))
        
        return f"err_{int(time.time())}_{id(error_event)}"
    
    def capture_message(
        self,
        message: str,
        level: str = "info",
        context: Optional[Dict[str, Any]] = None
    ):
        """Log a message with context."""
        log_method = getattr(logger, level, logger.info)
        log_method(message, extra={"context": context or {}})
        
        if self._sentry_client and level in ("warning", "error"):
            self._sentry_client.capture_message(message, level=level)
    
    def _should_alert(self, error_type: str) -> bool:
        """Check if we should send an alert for this error type."""
        count = self._error_counts[error_type]
        
        # Alert thresholds
        if count == 1:  # First occurrence
            return True
        if count == 5:  # 5 occurrences
            return True
        if count == 10:  # 10 occurrences
            return True
        if count % 50 == 0:  # Every 50 after that
            return True
        
        return False
    
    async def _send_alert(self, error: ErrorEvent):
        """Send alert to Slack and/or email."""
        # Check cooldown (don't spam)
        cooldown_key = f"{error.error_type}_{error.severity}"
        last_alert = self._alert_cooldown.get(cooldown_key)
        
        if last_alert and datetime.utcnow() - last_alert < timedelta(minutes=5):
            return  # Skip, too recent
        
        self._alert_cooldown[cooldown_key] = datetime.utcnow()
        
        # Send to Slack
        if ALERT_SLACK_WEBHOOK:
            await self._send_slack_alert(error)
    
    async def _send_slack_alert(self, error: ErrorEvent):
        """Send alert to Slack webhook."""
        message = {
            "text": f"[{error.severity.upper()}] {error.error_type}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{error.error_type}"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Severity:*\n{error.severity.upper()}"},
                        {"type": "mrkdwn", "text": f"*Service:*\n{error.service}"},
                        {"type": "mrkdwn", "text": f"*Time:*\n{error.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"},
                        {"type": "mrkdwn", "text": f"*Count:*\n{self._error_counts[error.error_type]}"},
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Message:*\n```{error.message[:500]}```"
                    }
                }
            ]
        }
        
        if error.context:
            context_str = json.dumps(error.context, indent=2, default=str)[:500]
            message["blocks"].append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Context:*\n```{context_str}```"
                }
            })
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(ALERT_SLACK_WEBHOOK, json=message, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")
    
    # ==================== METRICS ====================
    
    def record_metric(
        self,
        name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None
    ):
        """Record a metric value."""
        metric = MetricPoint(
            name=name,
            value=value,
            tags=tags or {}
        )
        
        self._metrics.append(metric)
        
        # Keep only last 10000 metrics in memory
        if len(self._metrics) > 10000:
            self._metrics = self._metrics[-10000:]
        
        # Log for debugging
        logger.debug(f"Metric: {name}={value} tags={tags}")
    
    def increment(self, name: str, value: float = 1, tags: Optional[Dict[str, str]] = None):
        """Increment a counter metric."""
        self.record_metric(f"{name}.count", value, tags)
    
    def timer(self, name: str, tags: Optional[Dict[str, str]] = None):
        """Context manager for timing operations."""
        return Timer(self, name, tags)
    
    def get_metrics_summary(
        self,
        since: Optional[datetime] = None,
        name_prefix: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get a summary of recorded metrics."""
        if since is None:
            since = datetime.utcnow() - timedelta(hours=1)
        
        filtered = [
            m for m in self._metrics
            if m.timestamp >= since and (not name_prefix or m.name.startswith(name_prefix))
        ]
        
        # Group by name
        by_name: Dict[str, List[float]] = defaultdict(list)
        for m in filtered:
            by_name[m.name].append(m.value)
        
        # Calculate stats
        summary = {}
        for name, values in by_name.items():
            summary[name] = {
                "count": len(values),
                "sum": sum(values),
                "avg": sum(values) / len(values) if values else 0,
                "min": min(values) if values else 0,
                "max": max(values) if values else 0,
            }
        
        return summary
    
    # ==================== HEALTH CHECKS ====================
    
    async def check_health(self) -> Dict[str, Any]:
        """Run health checks on all services."""
        from clearledgr.core.database import get_db
        
        health = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "checks": {}
        }
        
        # Database check
        try:
            db = get_db()
            db.initialize()
            health["checks"]["database"] = {"status": "healthy"}
        except Exception as e:
            health["checks"]["database"] = {"status": "unhealthy", "error": str(e)}
            health["status"] = "unhealthy"
        
        # Redis check (if used)
        # try:
        #     # Check Redis connection
        #     health["checks"]["redis"] = {"status": "healthy"}
        # except Exception as e:
        #     health["checks"]["redis"] = {"status": "unhealthy", "error": str(e)}
        
        # External services check
        external_checks = [
            ("gmail_api", "https://gmail.googleapis.com"),
            ("sheets_api", "https://sheets.googleapis.com"),
            ("slack_api", "https://slack.com/api"),
        ]
        
        async with httpx.AsyncClient() as client:
            for name, url in external_checks:
                try:
                    response = await client.head(url, timeout=5)
                    health["checks"][name] = {
                        "status": "healthy" if response.status_code < 500 else "degraded"
                    }
                except Exception as e:
                    health["checks"][name] = {"status": "degraded", "error": str(e)}
        
        # Error rate check
        recent_errors = [
            e for e in self._errors
            if e.timestamp > datetime.utcnow() - timedelta(minutes=5)
        ]
        
        if len(recent_errors) > 100:
            health["checks"]["error_rate"] = {
                "status": "unhealthy",
                "count": len(recent_errors),
                "threshold": 100
            }
            health["status"] = "unhealthy"
        elif len(recent_errors) > 50:
            health["checks"]["error_rate"] = {
                "status": "degraded",
                "count": len(recent_errors),
                "threshold": 50
            }
            if health["status"] == "healthy":
                health["status"] = "degraded"
        else:
            health["checks"]["error_rate"] = {
                "status": "healthy",
                "count": len(recent_errors)
            }
        
        return health
    
    # ==================== ERROR STATS ====================
    
    def get_error_summary(
        self,
        since: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get a summary of recent errors."""
        if since is None:
            since = datetime.utcnow() - timedelta(hours=24)
        
        recent = [e for e in self._errors if e.timestamp >= since]
        
        by_type: Dict[str, int] = defaultdict(int)
        by_severity: Dict[str, int] = defaultdict(int)
        
        for error in recent:
            by_type[error.error_type] += 1
            by_severity[error.severity] += 1
        
        return {
            "total": len(recent),
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
            "since": since.isoformat(),
        }


class Timer:
    """Context manager for timing operations."""
    
    def __init__(self, monitor: MonitoringService, name: str, tags: Optional[Dict[str, str]] = None):
        self.monitor = monitor
        self.name = name
        self.tags = tags or {}
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = (time.time() - self.start_time) * 1000  # ms
            self.monitor.record_metric(f"{self.name}.duration_ms", duration, self.tags)
        
        if exc_type:
            self.monitor.increment(f"{self.name}.errors", tags=self.tags)
        else:
            self.monitor.increment(f"{self.name}.success", tags=self.tags)
        
        return False  # Don't suppress exceptions
    
    async def __aenter__(self):
        self.start_time = time.time()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)


def track_errors(func: Callable) -> Callable:
    """Decorator to automatically track errors in a function."""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        monitor = get_monitor()
        try:
            with monitor.timer(f"function.{func.__name__}"):
                return await func(*args, **kwargs)
        except Exception as e:
            monitor.capture_error(e, context={"function": func.__name__, "args": str(args)[:200]})
            raise
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        monitor = get_monitor()
        try:
            with monitor.timer(f"function.{func.__name__}"):
                return func(*args, **kwargs)
        except Exception as e:
            monitor.capture_error(e, context={"function": func.__name__, "args": str(args)[:200]})
            raise
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper


# Global instance
_monitor: Optional[MonitoringService] = None


def get_monitor() -> MonitoringService:
    """Get the global monitoring service instance."""
    global _monitor
    if _monitor is None:
        _monitor = MonitoringService()
    return _monitor
