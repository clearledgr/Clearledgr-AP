"""Base contracts for finance-agent runtime skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class FinanceSkill(ABC):
    """Contract for skills hosted by the finance agent runtime."""

    @property
    @abstractmethod
    def skill_id(self) -> str:
        """Stable identifier for the skill implementation."""

    @property
    @abstractmethod
    def intents(self) -> frozenset[str]:
        """Intent ids handled by this skill."""

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
