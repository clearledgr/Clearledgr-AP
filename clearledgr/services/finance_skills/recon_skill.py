"""Reconciliation OperationalSkill for the FinanceAgentRuntime.

Handles intents: start_reconciliation, read_recon_status.
Registered alongside APFinanceSkill in the runtime's skill map.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from clearledgr.core.finance_contracts import (
    ActionExecution,
    SkillCapabilityManifest,
    SkillRequest,
    SkillResponse,
)
from clearledgr.services.finance_skills.base import FinanceSkill

if TYPE_CHECKING:
    from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime

logger = logging.getLogger(__name__)


class ReconciliationFinanceSkill(FinanceSkill):
    """Operational skill for reconciliation intents."""

    _INTENTS = frozenset({"start_reconciliation", "read_recon_status"})

    _MANIFEST = SkillCapabilityManifest(
        skill_id="recon_v1",
        version="1.0",
        state_machine={
            "states": ["imported", "matching", "matched", "exception", "review", "resolved", "posted"],
            "initial": "imported",
            "terminal": ["posted"],
            "transitions": {
                "imported": ["matching"],
                "matching": ["matched", "exception"],
                "matched": ["posted"],
                "exception": ["review", "matching"],
                "review": ["resolved", "matching"],
                "resolved": ["posted"],
            },
        },
        action_catalog=[
            {"id": "start_reconciliation", "label": "Start reconciliation from Google Sheet"},
            {"id": "read_recon_status", "label": "Get reconciliation session status"},
        ],
        adapter_bindings={"google_sheets": ["read", "write"]},
        kpi_contract={
            "promotion_gates": {
                "match_rate_min": 0.80,
            },
        },
    )

    @property
    def skill_id(self) -> str:
        return "recon_v1"

    @property
    def intents(self) -> frozenset:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    def policy_precheck(
        self, runtime: "FinanceAgentRuntime", request: SkillRequest
    ) -> Optional[SkillResponse]:
        """No policy gates for reconciliation v1."""
        return None

    def audit_contract(self, action: ActionExecution) -> Dict[str, Any]:
        return {
            "event_type": f"recon.{action.action_id}",
            "entity_type": "recon_session",
            "entity_id": action.entity_id,
        }

    def preview(
        self, runtime: "FinanceAgentRuntime", request: SkillRequest
    ) -> SkillResponse:
        """Preview what a reconciliation action would do."""
        intent = str(request.intent or "").strip().lower()
        payload = request.payload or {}

        if intent == "start_reconciliation":
            return SkillResponse(
                status="preview",
                details={
                    "action": "start_reconciliation",
                    "spreadsheet_id": payload.get("spreadsheet_id"),
                    "range": payload.get("range", "Sheet1!A:F"),
                    "description": "Will import transactions and match against posted AP items.",
                },
            )

        if intent == "read_recon_status":
            session_id = payload.get("session_id", "")
            session = runtime.db.get_recon_session(session_id)
            return SkillResponse(
                status="preview",
                details={"session": session or {"error": "not_found"}},
            )

        return SkillResponse(status="error", details={"error": "unknown_intent"})

    def execute(
        self, runtime: "FinanceAgentRuntime", request: SkillRequest
    ) -> SkillResponse:
        """Execute a reconciliation action."""
        intent = str(request.intent or "").strip().lower()
        payload = request.payload or {}

        if intent == "start_reconciliation":
            spreadsheet_id = str(payload.get("spreadsheet_id") or "").strip()
            sheet_range = str(payload.get("range") or "Sheet1!A:F").strip()

            if not spreadsheet_id:
                return SkillResponse(status="error", details={"error": "missing_spreadsheet_id"})

            session = runtime.db.create_recon_session(
                organization_id=runtime.organization_id,
                source_type="google_sheets",
                spreadsheet_id=spreadsheet_id,
                sheet_range=sheet_range,
            )

            return SkillResponse(
                status="started",
                details={
                    "session_id": session["id"],
                    "spreadsheet_id": spreadsheet_id,
                    "range": sheet_range,
                    "next_step": "Agent will import and match transactions.",
                },
            )

        if intent == "read_recon_status":
            session_id = str(payload.get("session_id") or "").strip()
            session = runtime.db.get_recon_session(session_id)
            if not session:
                return SkillResponse(status="error", details={"error": "session_not_found"})

            items = runtime.db.list_recon_items(session_id)
            return SkillResponse(
                status="ok",
                details={
                    "session": session,
                    "total": len(items),
                    "by_state": _count_by_state(items),
                },
            )

        return SkillResponse(status="error", details={"error": "unknown_intent"})

    def collect_runtime_metrics(
        self, runtime: "FinanceAgentRuntime", window_hours: int = 168
    ) -> Optional[Dict[str, Any]]:
        """Provide reconciliation-specific metrics for skill_readiness()."""
        # For now, return basic session counts
        try:
            with runtime.db.connect() as conn:
                row = conn.execute(
                    (
                        "SELECT COUNT(*) as total FROM recon_sessions WHERE organization_id = %s"
                    ),
                    (runtime.organization_id,),
                ).fetchone()
            total = row[0] if row else 0
        except Exception:
            total = 0

        return {
            "metrics": {"total_sessions": total},
            "gates": [],
            "status": "ready",
        }


def _count_by_state(items):
    counts = {}
    for item in items:
        state = item.get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts
