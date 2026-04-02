"""Structured error codes for Clearledgr AP.

Provides a canonical enum of error codes so that API consumers and
front-end clients can programmatically handle known failure modes
without parsing human-readable messages.

Usage (future -- callers will be migrated incrementally):

    from clearledgr.core.error_codes import ErrorCode

    raise AppError(ErrorCode.AP_ITEM_NOT_FOUND, detail="...")
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Machine-readable error codes returned in API error responses."""

    # ── Authentication / Authorization ────────────────────────────────
    AUTH_TOKEN_EXPIRED = "auth_token_expired"
    AUTH_TOKEN_INVALID = "auth_token_invalid"
    AUTH_INSUFFICIENT_ROLE = "auth_insufficient_role"
    AUTH_ORG_MISMATCH = "auth_org_mismatch"

    # ── AP Items ──────────────────────────────────────────────────────
    AP_ITEM_NOT_FOUND = "ap_item_not_found"
    AP_ITEM_DUPLICATE = "ap_item_duplicate"
    AP_INVALID_STATE_TRANSITION = "ap_invalid_state_transition"
    AP_FIELD_REVIEW_REQUIRED = "ap_field_review_required"
    AP_VALIDATION_FAILED = "ap_validation_failed"
    AP_CONFIDENCE_TOO_LOW = "ap_confidence_too_low"

    # ── ERP Integration ───────────────────────────────────────────────
    ERP_CONNECTION_MISSING = "erp_connection_missing"
    ERP_AUTH_EXPIRED = "erp_auth_expired"
    ERP_POST_FAILED = "erp_post_failed"
    ERP_VALIDATION_FAILED = "erp_validation_failed"

    # ── Gmail / Email ─────────────────────────────────────────────────
    GMAIL_TOKEN_MISSING = "gmail_token_missing"
    GMAIL_TOKEN_EXPIRED = "gmail_token_expired"
    GMAIL_THREAD_NOT_FOUND = "gmail_thread_not_found"

    # ── Approvals ─────────────────────────────────────────────────────
    APPROVAL_CHAIN_MISSING = "approval_chain_missing"
    APPROVAL_ALREADY_ACTIONED = "approval_already_actioned"

    # ── Rate Limiting ─────────────────────────────────────────────────
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"

    # ── Agent Runtime ─────────────────────────────────────────────────
    AGENT_TASK_TIMEOUT = "agent_task_timeout"
    AGENT_TASK_FAILED = "agent_task_failed"
    AGENT_HITL_REQUIRED = "agent_hitl_required"

    # ── General ───────────────────────────────────────────────────────
    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
