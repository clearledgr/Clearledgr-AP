"""Canonical AP workflow types for Temporal orchestration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class APWorkflowCommand:
    command: str
    organization_id: str
    ap_item_id: str
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex}")
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid.uuid4().hex}")
    actor_type: str = "system"
    actor_id: str = "workflow"
    payload: Dict[str, Any] = field(default_factory=dict)
    requested_at: str = field(default_factory=utcnow_iso)


@dataclass
class ApprovalDecision:
    ap_item_id: str
    run_id: str
    action: str
    actor_id: str
    actor_display: Optional[str] = None
    reason: Optional[str] = None
    source_channel: Optional[str] = None
    source_message_ref: Optional[str] = None
    request_ts: str = field(default_factory=utcnow_iso)


@dataclass
class ERPPostResult:
    status: str
    erp_reference_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    raw_response_redacted: Optional[Dict[str, Any]] = None


@dataclass
class AgentTraceEntry:
    step: str
    role: str
    summary: str
    model: Optional[str] = None
    tool: Optional[str] = None
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=utcnow_iso)


@dataclass
class TransitionEnvelope:
    ap_item_id: str
    workflow_id: str
    run_id: str
    state: str
    status: str = "accepted"
    detail: Optional[str] = None
    correlation_id: Optional[str] = None


def build_workflow_id(organization_id: str, ap_item_id: str) -> str:
    return f"ap:{organization_id}:{ap_item_id}"
