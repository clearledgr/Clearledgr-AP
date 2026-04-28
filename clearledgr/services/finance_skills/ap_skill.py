"""AP skill module for the finance-agent runtime."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from clearledgr.core.finance_contracts import SkillCapabilityManifest
from clearledgr.services.finance_skills.ap_intent_contracts import (
    build_operator_copy,
    get_intent_audit_contract,
)
from clearledgr.services.finance_skills.ap_intent_handlers import get_ap_intent_handler
from clearledgr.services.finance_skills.base import FinanceSkill
from clearledgr.services.invoice_workflow import get_invoice_workflow
from clearledgr.services.slack_api import resolve_slack_runtime
from clearledgr.services.slack_notifications import send_approval_reminder


class APFinanceSkill(FinanceSkill):
    """Finance skill for AP v1 operational intents."""

    _INTENTS = frozenset(
        {
            "request_approval",
            "approve_invoice",
            "request_info",
            "nudge_approval",
            "escalate_approval",
            "reassign_approval",
            "reject_invoice",
            "post_to_erp",
            "prepare_vendor_followups",
            "route_low_risk_for_approval",
            "retry_recoverable_failures",
            # Phase 2 audit-P0 intents (workspace SPA actions). These
            # were previously direct DB writes that bypassed the
            # runtime; promoting them to first-class intents adds
            # governance veto + agent memory + learning feedback on
            # top of the existing state-machine + atomic-audit
            # guarantees from update_ap_item.
            "snooze_invoice",
            "unsnooze_invoice",
            "reverse_invoice_post",
            "manually_classify_invoice",
            "resubmit_invoice",
            "split_invoice",
            "merge_invoices",
            "resolve_non_invoice_review",
            "resolve_entity_route",
            "update_invoice_fields",
        }
    )
    _MANIFEST = SkillCapabilityManifest(
        skill_id="ap_v1",
        version="1.0",
        state_machine={
            "primary_path": [
                "received",
                "validated",
                "needs_approval",
                "approved",
                "ready_to_post",
                "posted_to_erp",
                "closed",
            ],
            "exception_paths": [
                ["validated", "needs_info"],
                ["needs_approval", "rejected"],
                ["ready_to_post", "failed_post"],
                ["failed_post", "ready_to_post"],
                ["needs_info", "validated"],
            ],
            "resubmission": {
                "terminal_rejected": True,
                "linkage_fields": [
                    "supersedes_ap_item_id",
                    "supersedes_invoice_key",
                    "resubmission_reason",
                ],
            },
        },
        action_catalog=[
            {
                "intent": "request_approval",
                "class": "mutating",
                "description": "Route a validated AP item to the configured approval surface.",
            },
            {
                "intent": "approve_invoice",
                "class": "mutating",
                "description": "Record an approval decision from a channel surface and continue ERP posting flow.",
            },
            {
                "intent": "request_info",
                "class": "mutating",
                "description": "Return an AP item to needs-info with a recorded reason.",
            },
            {
                "intent": "nudge_approval",
                "class": "mutating",
                "description": "Send a reminder for an approval request that is still pending.",
            },
            {
                "intent": "escalate_approval",
                "class": "mutating",
                "description": "Escalate a stuck approval request for finance review.",
            },
            {
                "intent": "reassign_approval",
                "class": "mutating",
                "description": "Hand off a pending approval request to a new approver.",
            },
            {
                "intent": "reject_invoice",
                "class": "mutating",
                "description": "Reject an AP item with a recorded operator reason.",
            },
            {
                "intent": "post_to_erp",
                "class": "mutating",
                "description": "Post an approved AP item to ERP through the canonical workflow path.",
            },
            {
                "intent": "route_low_risk_for_approval",
                "class": "mutating",
                "description": "Route eligible AP items to approval surfaces.",
            },
            {
                "intent": "prepare_vendor_followups",
                "class": "mutating",
                "description": "Prepare vendor info-request follow-up draft with SLA safeguards.",
            },
            {
                "intent": "retry_recoverable_failures",
                "class": "mutating",
                "description": "Retry recoverable AP posting failures via canonical resume path.",
            },
            {
                "intent": "snooze_invoice",
                "class": "mutating",
                "description": "Snooze an AP item for a fixed duration; the reaper restores the prior state when the window expires.",
            },
            {
                "intent": "unsnooze_invoice",
                "class": "mutating",
                "description": "Manually unsnooze an AP item before its timer expires and restore the prior state.",
            },
            {
                "intent": "reverse_invoice_post",
                "class": "mutating",
                "description": "Reverse a posted bill via the override-window service before the window expires.",
            },
            {
                "intent": "manually_classify_invoice",
                "class": "mutating",
                "description": "Manually re-classify an AP item's document type and re-route via the planning engine.",
            },
            {
                "intent": "resubmit_invoice",
                "class": "mutating",
                "description": "Create a new AP item that supersedes a rejected one, preserving the audit chain.",
            },
            {
                "intent": "split_invoice",
                "class": "mutating",
                "description": "Split an AP item into separate items by source link (gmail thread / message / attachment).",
            },
            {
                "intent": "merge_invoices",
                "class": "mutating",
                "description": "Merge a source AP item into a target, moving source links and suppressing the source from the worklist.",
            },
            {
                "intent": "resolve_non_invoice_review",
                "class": "mutating",
                "description": "Resolve a non-invoice document with a type-aware outcome (apply / link / send to reconciliation / etc).",
            },
            {
                "intent": "resolve_entity_route",
                "class": "mutating",
                "description": "Assign an AP item to a specific legal entity from the entity-routing candidates.",
            },
            {
                "intent": "update_invoice_fields",
                "class": "mutating",
                "description": "Update header fields (vendor / number / amount / etc) with a column whitelist.",
            },
        ],
        policy_pack={
            "deterministic_prechecks": [
                "state_guard",
                "approval_waiting_guard",
                "posting_readiness_guard",
                "recoverability_guard",
                "followup_sla_guard",
                "followup_attempt_limit_guard",
                "approval_eligibility_guard",
            ],
            "hitl_gates": [
                "approval_required",
                "reject_reason_capture",
                "followup_reason_capture",
                "retry_recoverability_confirmation",
            ],
        },
        evidence_schema={
            "material_refs": [
                "ap_item_id",
                "email_id",
                "audit_event_id",
                "idempotency_key",
                "correlation_id",
            ],
            "optional_refs": [
                "draft_id",
                "erp_reference",
                "slack_ts",
                "teams_message_id",
            ],
        },
        adapter_bindings={
            "email": ["gmail"],
            "approval": ["slack", "teams", "email"],
            "erp": ["netsuite", "sap", "quickbooks", "xero"],
        },
        kpi_contract={
            "metrics": [
                "agentic_telemetry.straight_through_rate.rate",
                "agentic_telemetry.human_intervention_rate.rate",
                "on_time_approvals.rate",
                "post_failure_rate.rate_24h",
                "agentic_telemetry.top_blocker_reasons",
            ],
            "promotion_gates": {
                "legal_transition_correctness_min": 0.99,
                "audit_coverage_min": 0.99,
                "idempotency_integrity_min": 0.99,
                "operator_acceptance_min": 0.8,
                "enabled_connector_readiness_min": 1.0,
            },
        },
    )

    @property
    def skill_id(self) -> str:
        return "ap_v1"

    @property
    def intents(self) -> frozenset[str]:
        return self._INTENTS

    @property
    def manifest(self) -> SkillCapabilityManifest:
        return self._MANIFEST

    @staticmethod
    def load_org_settings(runtime) -> Dict[str, Any]:
        db = getattr(runtime, "db", None)
        organization_id = str(getattr(runtime, "organization_id", "") or "").strip()
        if not db or not organization_id or not hasattr(db, "get_organization"):
            return {}
        org = db.get_organization(organization_id) or {}
        settings = org.get("settings_json") or org.get("settings") or {}
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        return settings if isinstance(settings, dict) else {}

    @staticmethod
    def with_autonomy_policy(
        runtime,
        *,
        ap_item: Dict[str, Any],
        payload: Dict[str, Any],
        precheck: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        merged = dict(precheck or {})
        reason_codes = list(merged.get("reason_codes") or [])
        autonomous_requested = runtime.is_autonomous_request(payload)
        autonomy_policy = runtime.ap_autonomy_policy(
            vendor_name=ap_item.get("vendor_name") or ap_item.get("vendor"),
            action=action,
            autonomous_requested=autonomous_requested,
            ap_item=ap_item,
        )
        merged["autonomous_requested"] = autonomous_requested
        merged["autonomy_policy"] = autonomy_policy
        if autonomous_requested and not autonomy_policy.get("autonomous_allowed"):
            reason_codes.extend(
                [
                    "autonomy_gate_blocked",
                    f"autonomy_mode_{autonomy_policy.get('mode')}",
                    *(autonomy_policy.get("reason_codes") or []),
                ]
            )
            merged["eligible"] = False
        merged["reason_codes"] = list(dict.fromkeys([code for code in reason_codes if code]))
        if "eligible" not in merged:
            merged["eligible"] = len(merged["reason_codes"]) == 0
        return merged

    def get_workflow(self, runtime):
        return get_invoice_workflow(runtime.organization_id)

    def resolve_slack_runtime(self, runtime) -> Dict[str, Any]:
        return resolve_slack_runtime(runtime.organization_id)

    async def send_approval_reminder(self, **kwargs):
        return await send_approval_reminder(**kwargs)

    def policy_precheck(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        handler = get_ap_intent_handler(normalized_intent)
        return handler.policy_precheck(self, runtime, payload)

    def audit_contract(self, intent: str) -> Dict[str, Any]:
        return get_intent_audit_contract(intent)

    def preview(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        context = self.policy_precheck(runtime, normalized_intent, payload)
        ap_item_id = context["ap_item_id"]
        email_id = context["email_id"]
        precheck = context["policy_precheck"]
        status = "eligible" if precheck.get("eligible") else "blocked"
        operator_copy = build_operator_copy(normalized_intent, eligible=bool(precheck.get("eligible")))

        return {
            "skill_id": self.skill_id,
            "intent": normalized_intent,
            "mode": "preview",
            "status": status,
            "organization_id": runtime.organization_id,
            "email_id": email_id,
            "ap_item_id": ap_item_id,
            "policy_precheck": precheck,
            "audit_contract": self.audit_contract(normalized_intent),
            "next_step": "execute_intent",
            "operator_copy": operator_copy,
        }

    async def execute(
        self,
        runtime,
        intent: str,
        input_payload: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_intent = str(intent or "").strip().lower()
        payload = input_payload if isinstance(input_payload, dict) else {}
        handler = get_ap_intent_handler(normalized_intent)
        context = self.policy_precheck(runtime, normalized_intent, payload)
        return await handler.execute(
            self,
            runtime,
            context,
            idempotency_key=idempotency_key,
        )
