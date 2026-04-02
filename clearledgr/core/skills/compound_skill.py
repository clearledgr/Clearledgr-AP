"""Compound PlanningSkill that merges tools from multiple skills.

Enables cross-skill orchestration (AP -> Vendor Compliance -> Recon)
without modifying AgentPlanningEngine. The engine dispatches by
task_type; CompoundSkill registers under its own task_type and
exposes a merged tool catalogue.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from clearledgr.core.skills.base import AgentTool, AgentTask, PlanningSkill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter: wrap VendorComplianceSkill (operational) as a planning tool
# ---------------------------------------------------------------------------

async def _handle_vendor_compliance_snapshot(
    organization_id: str = "default",
    limit: int = 50,
    override_threshold: float = 0.25,
    **_kwargs,
) -> Dict[str, Any]:
    """Read-only vendor compliance health summary as a planning tool."""
    try:
        from clearledgr.services.finance_skills.vendor_compliance_skill import (
            VendorComplianceSkill,
        )

        # VendorComplianceSkill._build_health_summary expects a runtime
        # object with organization_id and a db attribute. Build a minimal
        # stand-in so we don't import the full FinanceAgentRuntime.
        class _RuntimeStub:
            def __init__(self, org_id: str) -> None:
                self.organization_id = org_id
                from clearledgr.core.database import get_db
                self.db = get_db()

        stub = _RuntimeStub(organization_id)
        skill = VendorComplianceSkill()
        summary = skill._build_health_summary(
            stub,
            limit=min(max(limit, 1), 200),
            override_threshold=max(0.0, min(override_threshold, 1.0)),
        )
        return {"ok": True, **summary}
    except Exception as exc:
        logger.warning("[CompoundSkill] vendor_compliance_snapshot failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# CompoundSkill
# ---------------------------------------------------------------------------

class CompoundSkill(PlanningSkill):
    """Merges AP + Vendor Compliance (+ optional Recon) tools.

    Registers as its own task_type ("compound_ap_compliance") so the
    planning engine dispatches to it for tasks that need cross-skill
    context. Does NOT modify agent_runtime.py.

    Usage::

        planner = get_planning_engine()
        planner.register_skill(CompoundSkill("acme-corp"))
    """

    def __init__(
        self,
        organization_id: str = "default",
        include_recon: bool = False,
    ) -> None:
        self.organization_id = organization_id
        self._include_recon = include_recon

    @property
    def skill_name(self) -> str:
        return "compound_ap_compliance"

    def get_tools(self) -> List[AgentTool]:
        from clearledgr.core.skills.ap_skill import APSkill

        ap_tools = APSkill(self.organization_id).get_tools()

        compliance_tool = AgentTool(
            name="vendor_compliance_snapshot",
            description=(
                "Get a vendor compliance health snapshot: override rates, "
                "missing contract limits, bank detail changes, anomaly flags. "
                "Use this after enrich_with_context to check if the vendor has "
                "compliance issues that should influence the routing decision."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max vendors to analyze (default 50).",
                    },
                    "override_threshold": {
                        "type": "number",
                        "description": "Override rate threshold (default 0.25).",
                    },
                },
                "required": [],
            },
            handler=_handle_vendor_compliance_snapshot,
        )

        tools = ap_tools + [compliance_tool]

        if self._include_recon:
            try:
                from clearledgr.core.skills.recon_skill import ReconciliationSkill
                tools.extend(ReconciliationSkill().get_tools())
            except Exception as exc:
                logger.debug("[CompoundSkill] recon tools unavailable: %s", exc)

        # Enforce unique tool names — duplicates would confuse Claude
        seen: set = set()
        deduped: List[AgentTool] = []
        for tool in tools:
            if tool.name not in seen:
                seen.add(tool.name)
                deduped.append(tool)
            else:
                logger.warning("[CompoundSkill] duplicate tool name %r — skipping", tool.name)
        return deduped

    def build_system_prompt(self, task: AgentTask) -> str:
        from clearledgr.core.skills.ap_skill import APSkill

        ap_prompt = APSkill(self.organization_id).build_system_prompt(task)

        addendum = """

Additional capabilities in this session:
- vendor_compliance_snapshot: Check vendor compliance health (override rates, missing contracts, bank changes).
  Call this after enrich_with_context if the vendor is flagged or has high risk.

Extended sequence (when vendor compliance is relevant):
1. enrich_with_context
2. vendor_compliance_snapshot (if vendor has risk flags or is new)
3. run_validation_gate
4. get_ap_decision (include compliance data in vendor_context)
5. request_vendor_info (if needs_info)
6. execute_routing

Rules:
- vendor_compliance_snapshot is optional — skip it for trusted, low-risk vendors
- If compliance snapshot shows high override rate or missing contracts, factor that into your routing decision
- Do NOT call vendor_compliance_snapshot more than once per invoice"""

        if self._include_recon:
            addendum += """

Reconciliation tools are also available (import_transactions, match_transactions, flag_exceptions, write_results).
Only use reconciliation tools if the task payload explicitly requests reconciliation."""

        return ap_prompt + addendum
