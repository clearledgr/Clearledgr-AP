"""Read-only workflow health skill for multi-workflow runtime expansion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clearledgr.core.finance_contracts import SkillCapabilityManifest
from clearledgr.services.finance_skills.base import FinanceSkill


class WorkflowHealthSkill(FinanceSkill):
    """Read-only finance skill that summarizes AP workflow health."""

    _INTENTS = frozenset({"read_ap_workflow_health"})
    _MANIFEST = SkillCapabilityManifest(
        skill_id="workflow_health_v1",
        version="1.0",
        state_machine={
            "type": "read_only",
            "notes": "No AP state transitions are performed by this skill.",
        },
        action_catalog=[
            {
                "intent": "read_ap_workflow_health",
                "class": "read_only",
                "description": "Read-only workflow health snapshot for AP queue diagnostics.",
            }
        ],
        policy_pack={
            "deterministic_prechecks": ["limit_bounds_guard"],
            "hitl_gates": [],
        },
        evidence_schema={
            "material_refs": ["summary.total_items", "summary.state_counts"],
            "optional_refs": ["summary.sample_item_ids"],
        },
        adapter_bindings={
            "email": ["gmail", "outlook"],
            "approval": ["slack", "teams", "email"],
            "erp": ["netsuite", "sap", "quickbooks", "xero"],
        },
        kpi_contract={
            "metrics": [
                "summary.total_items",
                "summary.state_counts",
                "summary.top_states",
            ],
            "promotion_gates": {
                "read_only_contract_compliance": 1.0,
            },
        },
    )

    @property
    def skill_id(self) -> str:
        return "workflow_health_v1"

    @property
    def intents(self) -> frozenset[str]:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    def policy_precheck(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        raw_limit = payload.get("limit", 200)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))
        return {
            "eligible": True,
            "reason_codes": [],
            "read_only": True,
            "limit": limit,
        }

    def audit_contract(self, intent: str) -> Dict[str, Any]:
        return {
            "source": "finance_agent_runtime",
            "idempotent": False,
            "mutates_ap_state": False,
            "events": [],
            "read_only": True,
            "intent": str(intent or "").strip().lower(),
        }

    def _build_health_summary(self, runtime, *, limit: int) -> Dict[str, Any]:
        items = []
        if hasattr(runtime.db, "list_ap_items"):
            try:
                items = runtime.db.list_ap_items(runtime.organization_id, limit=limit)
            except TypeError:
                items = runtime.db.list_ap_items(runtime.organization_id)
                items = items[:limit]
            except Exception:
                items = []
        elif hasattr(runtime.db, "list_ap_items_all"):
            try:
                items = runtime.db.list_ap_items_all(runtime.organization_id, limit=limit)
            except Exception:
                items = []

        states: Dict[str, int] = {}
        for item in items:
            token = str((item or {}).get("state") or "unknown").strip().lower() or "unknown"
            states[token] = states.get(token, 0) + 1

        top_states = sorted(states.items(), key=lambda pair: (-pair[1], pair[0]))
        return {
            "total_items": len(items),
            "state_counts": states,
            "top_states": [{"state": state, "count": count} for state, count in top_states[:5]],
            "sample_item_ids": [str((item or {}).get("id") or "") for item in items[:5] if item],
            "organization_id": runtime.organization_id,
            "limit": limit,
        }

    def preview(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        precheck = self.policy_precheck(runtime, normalized_intent, input_payload)
        summary = self._build_health_summary(runtime, limit=precheck["limit"])
        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "mode": "preview",
            "status": "ready",
            "organization_id": runtime.organization_id,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "summary": summary,
            "next_step": "execute_intent",
            "operator_copy": {
                "what_happened": "Generated a read-only AP workflow health snapshot.",
                "why_now": "This summarizes current state distribution and queue pressure.",
                "recommended_now": "Use this snapshot to prioritize which AP skill intent to run next.",
            },
        }

    async def execute(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        _ = idempotency_key
        normalized_intent = str(intent or "").strip().lower()
        precheck = self.policy_precheck(runtime, normalized_intent, input_payload)
        summary = self._build_health_summary(runtime, limit=precheck["limit"])
        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "status": "snapshot_ready",
            "organization_id": runtime.organization_id,
            "read_only": True,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "summary": summary,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
