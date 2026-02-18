"""
Clearledgr AP v1 Error Handling

AP-only error types with user-friendly messages and debugging context.
"""
from __future__ import annotations

from typing import Optional, Dict, Any
from enum import Enum
from fastapi import HTTPException


class ErrorCode(str, Enum):
    """Standardized error codes for client handling."""
    INVALID_CONFIG = "INVALID_CONFIG"
    INVALID_FIELD = "INVALID_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_DATE = "INVALID_DATE"
    INVALID_AMOUNT = "INVALID_AMOUNT"

    INVALID_API_KEY = "INVALID_API_KEY"
    RATE_LIMITED = "RATE_LIMITED"

    DATABASE_ERROR = "DATABASE_ERROR"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    SLACK_ERROR = "SLACK_ERROR"
    GMAIL_ERROR = "GMAIL_ERROR"
    ERP_ERROR = "ERP_ERROR"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"


class ClearledgrError(Exception):
    """Base exception with structured error info."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.detail = detail
        self.context = context or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "error": self.code.value,
            "message": self.message,
        }
        if self.detail:
            result["detail"] = self.detail
        if self.context:
            result["context"] = self.context
        return result


class ConfigError(ClearledgrError):
    def __init__(self, field: str, detail: str):
        super().__init__(
            code=ErrorCode.INVALID_CONFIG,
            message=f"Invalid configuration for '{field}'",
            detail=detail,
            context={"field": field},
        )


class MissingFieldError(ClearledgrError):
    def __init__(self, field: str, detail: str = "Missing required field"):
        super().__init__(
            code=ErrorCode.MISSING_FIELD,
            message=f"Missing required field: {field}",
            detail=detail,
            context={"field": field},
        )


class DateFormatError(ClearledgrError):
    def __init__(self, value: str, expected: str = "YYYY-MM-DD"):
        super().__init__(
            code=ErrorCode.INVALID_DATE,
            message=f"Invalid date format: '{value}'",
            detail=f"Expected format: {expected}",
            context={"value": value, "expected": expected},
        )


class AmountFormatError(ClearledgrError):
    def __init__(self, value: str):
        super().__init__(
            code=ErrorCode.INVALID_AMOUNT,
            message="Invalid amount value",
            detail=f"Could not parse amount: {value}",
            context={"value": value},
        )


class ExternalServiceError(ClearledgrError):
    def __init__(self, service: str, detail: str):
        code_map = {
            "slack": ErrorCode.SLACK_ERROR,
            "gmail": ErrorCode.GMAIL_ERROR,
            "erp": ErrorCode.ERP_ERROR,
        }
        super().__init__(
            code=code_map.get(service.lower(), ErrorCode.NOTIFICATION_FAILED),
            message=f"{service} integration error",
            detail=detail,
            context={"service": service},
        )


def to_http_exception(error: ClearledgrError) -> HTTPException:
    status_map = {
        ErrorCode.INVALID_CONFIG: 400,
        ErrorCode.INVALID_FIELD: 400,
        ErrorCode.MISSING_FIELD: 400,
        ErrorCode.INVALID_DATE: 400,
        ErrorCode.INVALID_AMOUNT: 400,
        ErrorCode.INVALID_API_KEY: 401,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.DATABASE_ERROR: 500,
        ErrorCode.NOTIFICATION_FAILED: 500,
        ErrorCode.SLACK_ERROR: 502,
        ErrorCode.GMAIL_ERROR: 502,
        ErrorCode.ERP_ERROR: 502,
        ErrorCode.LLM_UNAVAILABLE: 503,
    }

    return HTTPException(
        status_code=status_map.get(error.code, 500),
        detail=error.to_dict(),
    )
