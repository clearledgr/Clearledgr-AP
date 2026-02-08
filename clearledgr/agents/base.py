"""Base agent for Clearledgr workflows."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from clearledgr.services.audit import AuditTrailService


@dataclass
class AgentContext:
    organization_id: Optional[str] = None
    requester: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=dict)
    audit: Optional[AuditTrailService] = None


class BaseAgent:
    name = "BaseAgent"

    def validate(self, ctx: AgentContext) -> None:
        """Validate inputs before execution."""

    def execute(self, ctx: AgentContext) -> Dict[str, Any]:
        """Execute agent logic."""
        raise NotImplementedError

    def log_event(
        self,
        ctx: AgentContext,
        action: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not ctx.audit:
            return
        ctx.audit.record_event(
            user_email=ctx.requester or "system",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            organization_id=ctx.organization_id,
            metadata=metadata,
        )
