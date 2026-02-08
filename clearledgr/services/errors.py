"""
Clearledgr Error Handling

Specific error types with user-friendly messages and debugging context.
"""
from typing import Optional, Dict, Any
from fastapi import HTTPException
from enum import Enum


class ErrorCode(str, Enum):
    """Standardized error codes for client handling."""
    # Input errors (400s)
    INVALID_CSV = "INVALID_CSV"
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_DATE = "INVALID_DATE"
    MISSING_FIELD = "MISSING_FIELD"
    EMPTY_DATA = "EMPTY_DATA"
    
    # Auth errors (401/403)
    INVALID_API_KEY = "INVALID_API_KEY"
    RATE_LIMITED = "RATE_LIMITED"
    
    # Processing errors (500s)
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    CATEGORIZATION_FAILED = "CATEGORIZATION_FAILED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    DATABASE_ERROR = "DATABASE_ERROR"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    
    # External service errors
    SHEETS_ERROR = "SHEETS_ERROR"
    EXCEL_ERROR = "EXCEL_ERROR"
    SLACK_ERROR = "SLACK_ERROR"
    TEAMS_ERROR = "TEAMS_ERROR"


class ClearledgrError(Exception):
    """Base exception with structured error info."""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.code = code
        self.message = message
        self.detail = detail
        self.context = context or {}
        super().__init__(message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to API response format."""
        result = {
            "error": self.code.value,
            "message": self.message
        }
        if self.detail:
            result["detail"] = self.detail
        if self.context:
            result["context"] = self.context
        return result


class CSVParseError(ClearledgrError):
    """Error parsing CSV file."""
    
    def __init__(self, source: str, detail: str):
        super().__init__(
            code=ErrorCode.INVALID_CSV,
            message=f"Could not parse {source} CSV file",
            detail=detail,
            context={"source": source}
        )


class ConfigError(ClearledgrError):
    """Error in configuration."""
    
    def __init__(self, field: str, detail: str):
        super().__init__(
            code=ErrorCode.INVALID_CONFIG,
            message=f"Invalid configuration for '{field}'",
            detail=detail,
            context={"field": field}
        )


class DateFormatError(ClearledgrError):
    """Error in date format."""
    
    def __init__(self, value: str, expected: str = "YYYY-MM-DD"):
        super().__init__(
            code=ErrorCode.INVALID_DATE,
            message=f"Invalid date format: '{value}'",
            detail=f"Expected format: {expected}",
            context={"value": value, "expected": expected}
        )


class EmptyDataError(ClearledgrError):
    """No data to process."""
    
    def __init__(self, source: str):
        super().__init__(
            code=ErrorCode.EMPTY_DATA,
            message=f"No data found in {source}",
            detail="File was parsed but contained no rows",
            context={"source": source}
        )


class ReconciliationError(ClearledgrError):
    """Error during reconciliation."""
    
    def __init__(self, stage: str, detail: str):
        super().__init__(
            code=ErrorCode.RECONCILIATION_FAILED,
            message=f"Reconciliation failed at {stage}",
            detail=detail,
            context={"stage": stage}
        )


class CategorizationError(ClearledgrError):
    """Error during categorization."""
    
    def __init__(self, detail: str):
        super().__init__(
            code=ErrorCode.CATEGORIZATION_FAILED,
            message="Transaction categorization failed",
            detail=detail
        )


class LLMError(ClearledgrError):
    """Error calling LLM service."""
    
    def __init__(self, detail: str):
        super().__init__(
            code=ErrorCode.LLM_UNAVAILABLE,
            message="AI explanation service unavailable",
            detail=detail
        )


class ExternalServiceError(ClearledgrError):
    """Error with external service (Sheets, Slack, etc)."""
    
    def __init__(self, service: str, detail: str):
        code_map = {
            "sheets": ErrorCode.SHEETS_ERROR,
            "excel": ErrorCode.EXCEL_ERROR,
            "slack": ErrorCode.SLACK_ERROR,
            "teams": ErrorCode.TEAMS_ERROR
        }
        super().__init__(
            code=code_map.get(service.lower(), ErrorCode.NOTIFICATION_FAILED),
            message=f"{service} integration error",
            detail=detail,
            context={"service": service}
        )


def to_http_exception(error: ClearledgrError) -> HTTPException:
    """Convert ClearledgrError to HTTPException."""
    # Map error codes to HTTP status codes
    status_map = {
        ErrorCode.INVALID_CSV: 400,
        ErrorCode.INVALID_CONFIG: 400,
        ErrorCode.INVALID_DATE: 400,
        ErrorCode.MISSING_FIELD: 400,
        ErrorCode.EMPTY_DATA: 400,
        ErrorCode.INVALID_API_KEY: 401,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.RECONCILIATION_FAILED: 500,
        ErrorCode.CATEGORIZATION_FAILED: 500,
        ErrorCode.LLM_UNAVAILABLE: 503,
        ErrorCode.DATABASE_ERROR: 500,
        ErrorCode.NOTIFICATION_FAILED: 500,
        ErrorCode.SHEETS_ERROR: 502,
        ErrorCode.EXCEL_ERROR: 502,
        ErrorCode.SLACK_ERROR: 502,
        ErrorCode.TEAMS_ERROR: 502,
    }
    
    return HTTPException(
        status_code=status_map.get(error.code, 500),
        detail=error.to_dict()
    )


def handle_safely(operation: str):
    """
    Decorator to handle errors gracefully with context.
    
    Usage:
        @handle_safely("reconciliation")
        async def run_reconciliation(...):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except ClearledgrError:
                raise
            except HTTPException:
                raise
            except Exception as e:
                raise ReconciliationError(
                    stage=operation,
                    detail=str(e)
                )
        return wrapper
    return decorator

