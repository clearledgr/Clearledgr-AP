"""Base contracts for finance-agent runtime skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

from clearledgr.core.finance_contracts import (
    ActionExecution,
    SkillCapabilityManifest,
    SkillRequest,
    SkillResponse,
)

if TYPE_CHECKING:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class FinanceSkill(ABC):
    """Contract for operational skills hosted by the finance agent runtime.

    Also known as OperationalSkill. Provides preview/execute for runtime intents.

    For the planning skill ABC (Claude tool-use in AgentPlanningEngine),
    see clearledgr.core.skills.base.FinanceSkill (PlanningSkill).
    """

    @property
    @abstractmethod
    def skill_id(self) -> str:
        """Stable identifier for the skill implementation."""

    @property
    @abstractmethod
    def intents(self) -> frozenset[str]:
        """Intent ids handled by this skill."""

    @property
    @abstractmethod
    def manifest(self) -> SkillCapabilityManifest:
        """Capability package required for skill promotion and readiness checks."""

    def supports_intent(self, intent: str) -> bool:
        normalized = str(intent or "").strip().lower()
        return normalized in self.intents

    @abstractmethod
    def policy_precheck(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return deterministic policy precheck and normalized context."""

    @abstractmethod
    def audit_contract(self, intent: str) -> Dict[str, Any]:
        """Return the audit/write contract for this intent."""

    @abstractmethod
    def preview(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return a preview response for the intent."""

    @abstractmethod
    async def execute(
        self,
        runtime: "FinanceAgentRuntime",
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the intent."""

    # Canonical wrappers: keep legacy intent/payload methods for compatibility
    # while enforcing one runtime contract for callers.
    def preview_contract(
        self,
        runtime: "FinanceAgentRuntime",
        request: SkillRequest,
    ) -> SkillResponse:
        legacy = self.preview(runtime, request.task_type, request.payload)
        response = SkillResponse.from_legacy(
            legacy if isinstance(legacy, dict) else {},
            fallback_status="blocked",
            default_recommended_action=request.task_type,
        )
        details = response.details
        details.setdefault("org_id", request.org_id)
        details.setdefault("skill_id", request.skill_id)
        details.setdefault("task_type", request.task_type)
        details.setdefault("entity_id", request.entity_id)
        details.setdefault("correlation_id", request.correlation_id)
        response.details = details
        return response

    async def execute_contract(
        self,
        runtime: "FinanceAgentRuntime",
        request: SkillRequest,
        action: ActionExecution,
    ) -> SkillResponse:
        legacy = await self.execute(
            runtime,
            request.task_type,
            request.payload,
            idempotency_key=action.idempotency_key,
        )
        response = SkillResponse.from_legacy(
            legacy if isinstance(legacy, dict) else {},
            fallback_status="failed",
            default_recommended_action=request.task_type,
        )
        details = response.details
        details.setdefault("org_id", request.org_id)
        details.setdefault("skill_id", request.skill_id)
        details.setdefault("task_type", request.task_type)
        details.setdefault("entity_id", action.entity_id or request.entity_id)
        details.setdefault("correlation_id", request.correlation_id)
        details.setdefault("action_execution", action.to_dict())
        response.details = details
        return response

    # ---- Optional: workflow-specific metrics & entity resolution ----
    # Override in subclass to provide workflow KPIs for skill_readiness().
    # Default returns None (manifest-only skill).

    def collect_runtime_metrics(
        self, runtime: "FinanceAgentRuntime", window_hours: int = 168
    ) -> Optional[Dict[str, Any]]:
        """Override to provide workflow-specific KPI collection for readiness gates."""
        return None

    def resolve_entity(
        self, runtime: "FinanceAgentRuntime", reference: str
    ) -> Dict[str, Any]:
        """Override to resolve a workflow entity by reference (e.g., AP item by ID)."""
        raise LookupError("entity_resolution_not_implemented")


# Alias for clarity — OperationalSkill is the skill type used by FinanceAgentRuntime
OperationalSkill = FinanceSkill
