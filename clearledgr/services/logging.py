"""
Structured logging for Clearledgr AP v1.
"""
import logging
import sys
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import os

# Log level from environment
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure root logger
logger = logging.getLogger("clearledgr")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Remove existing handlers
logger.handlers.clear()

# Create console handler with structured format
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Structured JSON formatter for production
class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data)

# Use JSON formatter in production, simple formatter in development
USE_JSON_LOGS = os.getenv("USE_JSON_LOGS", "false").lower() == "true"

if USE_JSON_LOGS:
    formatter = JSONFormatter()
else:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Prevent propagation to root logger
logger.propagate = False


def log_request(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    client_id: Optional[str] = None,
    **kwargs
):
    """Log HTTP request."""
    extra_fields = {
        "type": "http_request",
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
    }
    if client_id:
        extra_fields["client_id"] = client_id
    extra_fields.update(kwargs)
    
    record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=f"{method} {path} {status_code}",
        args=(),
        exc_info=None,
    )
    record.extra_fields = extra_fields
    logger.handle(record)


def log_error(
    error_type: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    exception: Optional[Exception] = None
):
    """Log error with context."""
    extra_fields = {
        "type": "error",
        "error_type": error_type,
    }
    if context:
        extra_fields.update(context)
    
    level = logging.ERROR
    if exception:
        logger.exception(message, extra={"extra_fields": extra_fields})
    else:
        record = logging.LogRecord(
            name=logger.name,
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        record.extra_fields = extra_fields
        logger.handle(record)
