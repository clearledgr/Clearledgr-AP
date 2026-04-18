"""Tests for the finance agent runtime contract (preview/execute)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class _FakeDB:
    def __init__(self) -> None:
        self.organization = {"id": "default", "settings": {"auto_approve_threshold": 0.91}}
        self.ap_kpis = {
            "organization_id": "default",
            "agentic_telemetry": {
                "agent_suggestion_acceptance": {
                    "prompted_count": 10,
                    "accepted_count": 8,
                    "rate": 0.8,
                },
                "extraction_drift": {
                    "summary": {
                        "vendors_monitored": 0,
                        "vendors_at_risk": 0,
                        "high_risk_vendors": 0,
                        "recent_open_blocked_items": 0,
                    },
                    "vendor_scorecards": [],
                    "sampled_review_queue": [],
                },
                "shadow_decision_scoring": {
                    "summary": {
                        "scored_item_count": 0,
                        "action_population": 0,
                        "action_match_count": 0,
                        "action_match_rate": 0.0,
                        "critical_field_population": 0,
                        "critical_field_match_count": 0,
                        "critical_field_match_rate": 0.0,
                        "corrected_item_count": 0,
                        "disagreement_count": 0,
                    },
                    "vendor_scorecards": [],
                    "sampled_disagreements": [],
                },
                "post_action_verification": {
                    "summary": {
                        "attempted_count": 0,
                        "verified_count": 0,
                        "mismatch_count": 0,
                        "verification_rate": 0.0,
                        "success_event_count": 0,
                        "failed_event_count": 0,
                    },
                    "vendor_scorecards": [],
                    "sampled_mismatches": [],
                },
            },
        }
        self.items = {
            "ap-route-1": {
                "id": "ap-route-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-route-1",
                "state": "validated",
                "vendor_name": "Runtime Co",
                "invoice_number": "INV-RT-1",
                "amount": 123.45,
                "currency": "USD",
                "metadata": {"correlation_id": "corr-runtime-route-1"},
            },
            "ap-followup-1": {
                "id": "ap-followup-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-followup-1",
                "state": "needs_info",
                "vendor_name": "Northwind",
                "invoice_number": "INV-FOLLOW-1",
                "amount": 120.0,
                "currency": "USD",
                "sender": "billing@northwind.example",
                "subject": "Need details",
                "user_id": "finance-user",
                "metadata": {
                    "correlation_id": "corr-runtime-followup-1",
                    "needs_info_question": "Please provide the PO number.",
                },
            },
            "ap-retry-1": {
                "id": "ap-retry-1",
                "organization_id": "default",
                "thread_id": "gmail-thread-retry-1",
                "state": "failed_post",
                "vendor_name": "Retry Co",
                "invoice_number": "INV-RETRY-1",
                "amount": 141.0,
                "currency": "USD",
                "last_error": "connector timeout",
                "metadata": {"correlation_id": "corr-runtime-retry-1"},
            },
        }
        self.audit_rows = []
        self.source_rows = []
        self.slack_threads = {}
        self.reassigned_chains = []

    def _all_items(self):
        return list(self.items.values())

    def get_ap_item(self, item_id):
        token = str(item_id or "")
        for item in self._all_items():
            if token in {str(item.get("id")), str(item.get("thread_id")), str(item.get("message_id") or "")}:
                return item
        return None

    def get_ap_item_by_thread(self, organization_id, thread_id):
        org = str(organization_id or "")
        token = str(thread_id or "")
        for item in self._all_items():
            if str(item.get("organization_id") or "") != org:
                continue
            if token == str(item.get("thread_id") or ""):
                return item
            if any(
                str(row.get("ap_item_id") or "") == str(item.get("id") or "")
                and str(row.get("source_type") or "") == "gmail_thread"
                and str(row.get("source_ref") or "") == token
                for row in self.source_rows
            ):
                return item
        return None

    def get_ap_item_by_message_id(self, organization_id, message_id):
        org = str(organization_id or "")
        token = str(message_id or "")
        for item in self._all_items():
            if str(item.get("organization_id") or "") != org:
                continue
            if token == str(item.get("message_id") or ""):
                return item
            if any(
                str(row.get("ap_item_id") or "") == str(item.get("id") or "")
                and str(row.get("source_type") or "") == "gmail_message"
                and str(row.get("source_ref") or "") == token
                for row in self.source_rows
            ):
                return item
        return None

    def get_organization(self, organization_id):
        if str(organization_id or "") != "default":
            return None
        return dict(self.organization)

    def update_ap_item(self, ap_item_id, **kwargs):
        token = str(ap_item_id or "")
        item = self.items.get(token)
        if not item:
            return False
        for key, value in (kwargs or {}).items():
            item[key] = value
        return True

    def update_ap_item_metadata_merge(self, ap_item_id, patch):
        token = str(ap_item_id or "")
        item = self.items.get(token)
        if not item:
            return False
        metadata = dict(item.get("metadata") or {})
        metadata.update(dict(patch or {}))
        item["metadata"] = metadata
        return True

    def create_ap_item(self, payload):
        item_id = str((payload or {}).get("id") or f"ap-created-{len(self.items) + 1}")
        item = {
            "id": item_id,
            "organization_id": (payload or {}).get("organization_id", "default"),
            "thread_id": (payload or {}).get("thread_id"),
            "message_id": (payload or {}).get("message_id"),
            "state": (payload or {}).get("state", "received"),
            "vendor_name": (payload or {}).get("vendor_name"),
            "invoice_number": (payload or {}).get("invoice_number"),
            "amount": (payload or {}).get("amount"),
            "currency": (payload or {}).get("currency"),
            "subject": (payload or {}).get("subject"),
            "sender": (payload or {}).get("sender"),
            "confidence": (payload or {}).get("confidence", 0.0),
            "field_confidences": (payload or {}).get("field_confidences"),
            "exception_code": (payload or {}).get("exception_code"),
            "exception_severity": (payload or {}).get("exception_severity"),
            "metadata": (payload or {}).get("metadata", {}),
        }
        self.items[item_id] = item
        return item

    def link_ap_item_source(self, payload):
        row = dict(payload or {})
        self.source_rows.append(row)
        return row

    def list_ap_item_sources(self, ap_item_id):
        token = str(ap_item_id or "")
        return [row for row in self.source_rows if str(row.get("ap_item_id") or "") == token]

    def get_ap_audit_event_by_key(self, idempotency_key):
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        for row in self.audit_rows:
            if str(row.get("idempotency_key") or "").strip() == key:
                return row
        return None

    def append_audit_event(self, payload):
        key = str((payload or {}).get("idempotency_key") or "").strip()
        if key:
            existing = self.get_ap_audit_event_by_key(key)
            if existing:
                return existing
        row = {
            "id": f"audit-{len(self.audit_rows) + 1}",
            **dict(payload or {}),
        }
        if "payload_json" not in row:
            row["payload_json"] = dict(row.get("metadata") or {})
        self.audit_rows.append(row)
        return row

    def list_ap_audit_events(self, ap_item_id):
        token = str(ap_item_id or "")
        return [
            row
            for row in self.audit_rows
            if str(row.get("ap_item_id") or "") == token
        ]

    def get_ap_kpis(self, organization_id, approval_sla_minutes=240):
        _ = approval_sla_minutes
        payload = dict(self.ap_kpis)
        payload["organization_id"] = organization_id
        return payload

    def get_slack_thread(self, email_id):
        return self.slack_threads.get(str(email_id or ""))

    def db_reassign_pending_step_approvers(self, chain_id, approvers, comments=""):
        self.reassigned_chains.append(
            {
                "chain_id": chain_id,
                "approvers": list(approvers or []),
                "comments": comments,
            }
        )
        return True

    def get_operational_metrics(self, organization_id, approval_sla_minutes=240, workflow_stuck_minutes=120):
        _ = approval_sla_minutes, workflow_stuck_minutes
        return {
            "organization_id": organization_id,
            "queue_lag": {"avg_minutes": 5.0},
            "post_failure_rate": {"rate_24h": 0.02},
        }

    def list_ap_items(self, organization_id, state=None, limit=200, prioritized=False):
        _ = prioritized
        org = str(organization_id or "")
        rows = [item for item in self._all_items() if str(item.get("organization_id") or "") == org]
        if state:
            token = str(state).strip().lower()
            rows = [item for item in rows if str(item.get("state") or "").strip().lower() == token]
        return rows[: max(1, int(limit or 200))]


def _runtime(db: _FakeDB) -> FinanceAgentRuntime:
    return FinanceAgentRuntime(
        organization_id="default",
        actor_id="user-1",
        actor_email="agent@example.com",
        db=db,
    )


def test_runtime_registers_ap_and_read_only_health_skills():
    db = _FakeDB()
    runtime = _runtime(db)
    assert "escalate_approval" in runtime.supported_intents
    assert "reassign_approval" in runtime.supported_intents
    assert "prepare_vendor_followups" in runtime.supported_intents
    assert "route_low_risk_for_approval" in runtime.supported_intents
    assert "retry_recoverable_failures" in runtime.supported_intents
    assert "read_vendor_compliance_health" in runtime.supported_intents
    assert "read_ap_workflow_health" in runtime.supported_intents


def test_runtime_list_skills_returns_manifest_contracts():
    db = _FakeDB()
    runtime = _runtime(db)

    rows = runtime.list_skills()

    assert rows
    ap_skill = next(row for row in rows if row["skill_id"] == "ap_v1")
    assert ap_skill["manifest"]["is_valid"] is True
    assert "state_machine" in ap_skill["manifest"]
    assert ap_skill["readiness"]["status"] == "manifest_valid"


def test_skill_readiness_reports_gate_statuses_for_ap_skill():
    db = _FakeDB()
    db.audit_rows.extend(
        [
            {
                "id": "audit-transition-pass",
                "ap_item_id": "ap-route-1",
                "event_type": "state_transition",
                "idempotency_key": "idem-transition-pass",
            },
            {
                "id": "audit-transition-rejected",
                "ap_item_id": "ap-route-1",
                "event_type": "state_transition_rejected",
                "decision_reason": "illegal_transition",
                "idempotency_key": "idem-transition-rejected",
            },
        ]
    )
    runtime = _runtime(db)

    readiness = runtime.skill_readiness("ap_v1")

    assert readiness["skill_id"] == "ap_v1"
    assert readiness["status"] == "blocked"
    gate_map = {gate["gate"]: gate for gate in readiness["gates"]}
    assert gate_map["operator_acceptance"]["status"] == "pass"
    assert gate_map["legal_transition_correctness"]["status"] in {"fail", "pass"}
    assert gate_map["enabled_connector_readiness"]["status"] in {
        "pass",
        "fail",
        "not_verifiable",
        "not_configured",
    }
    assert "metrics" in readiness


def test_ap_autonomy_policy_manual_when_readiness_gates_fail():
    db = _FakeDB()
    runtime = _runtime(db)
    readiness = {
        "status": "blocked",
        "gates": [
            {"gate": "audit_coverage", "status": "fail"},
            {"gate": "operator_acceptance", "status": "pass"},
        ],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="route_low_risk_for_approval",
            autonomous_requested=True,
        )

    assert policy["mode"] == "manual"
    assert policy["autonomous_allowed"] is False
    assert "ap_skill_not_ready" in policy["reason_codes"]
    assert "gate:audit_coverage" in policy["reason_codes"]


def test_ap_autonomy_policy_assisted_when_vendor_is_unscored():
    db = _FakeDB()
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="route_low_risk_for_approval",
            autonomous_requested=True,
        )

    assert policy["mode"] == "assisted"
    assert policy["autonomous_allowed"] is False
    assert "vendor_unscored" in policy["reason_codes"]


def test_ap_autonomy_policy_assisted_when_vendor_has_only_earned_autonomous_routing():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 0,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 5,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["shadow_decision_scoring"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "scored_item_count": 3,
            "action_match_rate": 0.86,
            "critical_field_match_rate": 0.94,
            "disagreement_count": 0,
        }
    ]
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="route_low_risk_for_approval",
            autonomous_requested=True,
        )

    assert policy["mode"] == "assisted"
    assert policy["autonomous_allowed"] is True
    assert "route_low_risk_for_approval" in policy["earned_actions"]
    assert "auto_approve" in policy["blocked_actions"]
    assert "post_to_erp" in policy["blocked_actions"]


def test_ap_autonomy_policy_auto_when_vendor_has_earned_approval_and_post():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 0,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 6,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["shadow_decision_scoring"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "scored_item_count": 6,
            "action_match_rate": 0.97,
            "critical_field_match_rate": 0.99,
            "disagreement_count": 0,
        }
    ]
    db.ap_kpis["agentic_telemetry"]["post_action_verification"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "attempted_count": 3,
            "verified_count": 3,
            "mismatch_count": 0,
            "verification_rate": 1.0,
        }
    ]
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="auto_approve_post",
            autonomous_requested=True,
        )

    assert policy["mode"] == "auto"
    assert policy["autonomous_allowed"] is True
    assert set(policy["earned_actions"]) == {
        "route_low_risk_for_approval",
        "auto_approve",
        "post_to_erp",
    }
    assert policy["reason_codes"] == []


def test_ap_autonomy_policy_blocks_item_when_linked_finance_effect_review_is_required():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 0,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 6,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["shadow_decision_scoring"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "scored_item_count": 6,
            "action_match_rate": 0.97,
            "critical_field_match_rate": 0.99,
            "disagreement_count": 0,
        }
    ]
    db.ap_kpis["agentic_telemetry"]["post_action_verification"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "attempted_count": 3,
            "verified_count": 3,
            "mismatch_count": 0,
            "verification_rate": 1.0,
        }
    ]
    db.items["ap-route-1"]["metadata"] = {
        **db.items["ap-route-1"]["metadata"],
        "finance_effect_review_required": True,
        "finance_effect_blockers": [
            {
                "code": "linked_credit_adjustment_present",
                "detail": "A linked credit note changes the payable amount and should be reviewed before invoice routing or posting.",
            }
        ],
        "finance_effect_summary": {
            "applied_credit_total": 30.0,
            "remaining_balance_amount": 93.45,
            "currency": "USD",
        },
    }
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="post_to_erp",
            autonomous_requested=True,
            ap_item=db.items["ap-route-1"],
        )

    assert policy["mode"] == "auto"
    assert policy["autonomous_allowed"] is False
    assert "linked_finance_effect_review_required" in policy["reason_codes"]
    assert "linked_credit_adjustment_present" in policy["reason_codes"]
    assert policy["finance_effect_summary"]["applied_credit_total"] == 30.0


def test_ap_autonomy_policy_manual_when_vendor_shadow_quality_is_low():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 0,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 6,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["shadow_decision_scoring"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "scored_item_count": 4,
            "action_match_rate": 0.5,
            "critical_field_match_rate": 0.8,
            "disagreement_count": 2,
        }
    ]
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="route_low_risk_for_approval",
            autonomous_requested=True,
        )

    assert policy["mode"] == "manual"
    assert policy["autonomous_allowed"] is False
    assert "vendor_shadow_action_match_low" in policy["reason_codes"] or "vendor_shadow_critical_field_match_low" in policy["reason_codes"]


def test_ap_autonomy_policy_manual_when_vendor_post_verification_is_low():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 0,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 6,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["post_action_verification"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "attempted_count": 3,
            "verified_count": 2,
            "mismatch_count": 1,
            "verification_rate": 0.6667,
        }
    ]
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        policy = runtime.ap_autonomy_policy(
            vendor_name="Runtime Co",
            action="post_to_erp",
            autonomous_requested=True,
        )

    assert policy["mode"] == "manual"
    assert policy["autonomous_allowed"] is False
    assert "vendor_post_verification_low" in policy["reason_codes"] or "vendor_post_verification_mismatch_present" in policy["reason_codes"]


def test_ap_autonomy_summary_surfaces_vendor_block_reasons_by_action():
    db = _FakeDB()
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["summary"] = {
        "vendors_monitored": 1,
        "vendors_at_risk": 1,
        "high_risk_vendors": 0,
        "recent_open_blocked_items": 0,
    }
    db.ap_kpis["agentic_telemetry"]["extraction_drift"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "drift_risk": "stable",
            "recent_invoice_count": 5,
            "sample_recommended_count": 0,
            "source_shift_fields": [],
        }
    ]
    db.ap_kpis["agentic_telemetry"]["shadow_decision_scoring"]["vendor_scorecards"] = [
        {
            "vendor_name": "Runtime Co",
            "scored_item_count": 4,
            "action_match_rate": 0.92,
            "critical_field_match_rate": 0.96,
            "disagreement_count": 0,
        }
    ]
    runtime = _runtime(db)
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        summary = runtime.ap_autonomy_summary()

    assert "action_thresholds" in summary
    assert "vendor_promotion_status" in summary
    vendor = summary["vendor_promotion_status"][0]
    assert vendor["vendor_name"] == "Runtime Co"
    assert vendor["mode"] == "assisted"
    assert vendor["action_policies"]["route_low_risk_for_approval"]["autonomous_allowed"] is True
    assert vendor["action_policies"]["post_to_erp"]["autonomous_allowed"] is False
    assert "vendor_post_verification_observation_mode" in vendor["blocked_actions"]["post_to_erp"]


def test_preview_route_low_risk_for_approval_returns_precheck():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = runtime.preview_intent(
            "route_low_risk_for_approval",
            {"email_id": "gmail-thread-route-1"},
        )

    assert result["intent"] == "route_low_risk_for_approval"
    assert result["mode"] == "preview"
    assert result["status"] == "eligible"
    assert result["policy_precheck"]["eligible"] is True


def test_execute_route_low_risk_for_approval_success_and_idempotent_replay():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.10"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        first = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-route-1",
            )
        )
        second = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-route-1",
            )
        )

    assert first["status"] == "pending_approval"
    assert first["audit_event_id"]
    assert first["agent_loop"]["owner"] == "finance_agent_loop"
    assert second["status"] == "pending_approval"
    assert second["idempotency_replayed"] is True
    assert second["agent_loop"]["idempotency_replayed"] is True
    assert len(db.audit_rows) == 1


def test_execute_route_low_risk_for_approval_returns_success_when_audit_append_fails():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.13"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch.object(runtime, "append_runtime_audit", side_effect=RuntimeError("audit_locked")):
            result = asyncio.run(
                runtime.execute_intent(
                    "route_low_risk_for_approval",
                    {"email_id": "gmail-thread-route-1"},
                    idempotency_key="idem-runtime-route-low-risk-audit-failure-1",
                )
            )

    assert result["status"] == "pending_approval"
    assert result["audit_status"] == "error"
    assert result["audit_error"] == "audit_locked"


def test_execute_request_approval_falls_back_to_email_reference_when_ap_item_id_is_stale():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.10"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "request_approval",
                {
                    "ap_item_id": "INV-RT-1",
                    "email_id": "gmail-thread-route-1",
                },
                idempotency_key="idem-runtime-request-approval-fallback-1",
            )
        )

    assert result["status"] == "pending_approval"
    assert result["ap_item_id"] == "ap-route-1"
    assert result["email_id"] == "gmail-thread-route-1"
    workflow._send_for_approval.assert_awaited_once()


def test_execute_request_approval_uses_resolved_email_reference_when_invoice_gmail_id_is_blank():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.11"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "request_approval",
                {
                    "ap_item_id": "ap-route-1",
                    "email_id": "gmail-thread-route-1",
                },
                idempotency_key="idem-runtime-request-approval-gmail-fallback-1",
            )
        )

    assert result["status"] == "pending_approval"
    workflow._send_for_approval.assert_awaited_once()
    invoice = workflow._send_for_approval.await_args.args[0]
    assert invoice.gmail_id == "gmail-thread-route-1"


def test_execute_request_approval_returns_success_when_audit_append_fails():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.12"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch.object(runtime, "append_runtime_audit", side_effect=RuntimeError("audit_locked")):
            result = asyncio.run(
                runtime.execute_intent(
                    "request_approval",
                    {
                        "ap_item_id": "ap-route-1",
                        "email_id": "gmail-thread-route-1",
                    },
                    idempotency_key="idem-runtime-request-approval-audit-failure-1",
                )
            )

    assert result["status"] == "pending_approval"
    assert result["audit_status"] == "error"
    assert result["audit_error"] == "audit_locked"


def test_execute_request_approval_blocks_until_entity_route_is_resolved():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["metadata"]["entity_candidates"] = [
        {"entity_code": "US-01", "entity_name": "Runtime Co US"},
        {"entity_code": "GH-01", "entity_name": "Runtime Co Ghana"},
    ]
    workflow = MagicMock()

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "request_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-request-approval-entity-block-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "entity_route_review_required"
    workflow._send_for_approval.assert_not_called()


def test_execute_request_approval_blocks_until_org_entity_rules_are_resolved():
    db = _FakeDB()
    db.organization["settings"] = {
        "auto_approve_threshold": 0.91,
        "entity_routing": {
            "entities": [
                {"entity_code": "US-01", "entity_name": "Runtime Co US"},
                {"entity_code": "GH-01", "entity_name": "Runtime Co Ghana"},
            ],
            "rules": [
                {
                    "entity_code": "US-01",
                    "sender_domains": ["us.runtime.example"],
                }
            ],
        },
    }
    db.items["ap-route-1"]["sender"] = "billing@unknown.runtime.example"
    runtime = _runtime(db)
    workflow = MagicMock()

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "request_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-request-approval-org-entity-block-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "entity_route_review_required"
    workflow._send_for_approval.assert_not_called()


def test_execute_route_low_risk_for_approval_blocks_field_review_precheck():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": False,
        "reason_codes": ["field_review_required", "blocking_source_conflicts"],
        "blocked_fields": ["amount"],
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-route-blocked-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"
    assert result["audit_event_id"]
    workflow._send_for_approval.assert_not_called()


def test_execute_route_low_risk_for_approval_blocks_unvalidated_items_explicitly():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": False,
        "reason_codes": ["state_not_validated"],
        "state": "received",
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-route-blocked-state-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "state_not_validated"
    assert result["audit_event_id"]
    workflow._send_for_approval.assert_not_called()


def test_execute_escalate_approval_updates_followup_metadata():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    workflow = MagicMock()

    async def _fake_send(payload):
        return {
            "status": "sent",
            "channel": payload.get("channel"),
            "email_id": payload.get("email_id"),
        }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.workflows.gmail_activities.send_slack_notification_activity", _fake_send):
            result = asyncio.run(
                runtime.execute_intent(
                    "escalate_approval",
                    {"email_id": "gmail-thread-route-1"},
                    idempotency_key="idem-runtime-escalate-approval-1",
                )
            )

    assert result["status"] == "escalated"
    assert result["audit_event_id"]
    assert db.items["ap-route-1"]["metadata"]["approval_escalation_count"] == 1
    assert db.items["ap-route-1"]["metadata"]["approval_next_action"] == "wait_for_escalated_review"


def test_execute_escalate_approval_dedupes_without_incrementing_followup_metadata():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    db.items["ap-route-1"]["metadata"]["approval_escalation_count"] = 2
    workflow = MagicMock()

    async def _fake_send(payload):
        return {
            "status": "deduped",
            "delivered": True,
            "channel": payload.get("channel"),
            "email_id": payload.get("email_id"),
            "thread_ts": "170.123",
        }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.workflows.gmail_activities.send_slack_notification_activity", _fake_send):
            result = asyncio.run(
                runtime.execute_intent(
                    "escalate_approval",
                    {"email_id": "gmail-thread-route-1"},
                    idempotency_key="idem-runtime-escalate-approval-deduped-1",
                )
            )

    assert result["status"] == "deduped"
    assert result["audit_event_id"]
    assert db.items["ap-route-1"]["metadata"]["approval_escalation_count"] == 2
    assert db.audit_rows[-1]["event_type"] == "approval_escalation_deduped"


def test_execute_escalate_approval_returns_success_when_audit_append_fails():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    workflow = MagicMock()

    async def _fake_send(payload):
        return {
            "status": "sent",
            "channel": payload.get("channel"),
            "email_id": payload.get("email_id"),
        }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.workflows.gmail_activities.send_slack_notification_activity", _fake_send):
            with patch.object(runtime, "append_runtime_audit", side_effect=RuntimeError("audit_locked")):
                result = asyncio.run(
                    runtime.execute_intent(
                        "escalate_approval",
                        {"email_id": "gmail-thread-route-1"},
                        idempotency_key="idem-runtime-escalate-approval-audit-failure-1",
                    )
                )

    assert result["status"] == "escalated"
    assert result["audit_status"] == "error"
    assert result["audit_error"] == "audit_locked"


def test_execute_nudge_approval_falls_back_without_slack_thread():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    db.items["ap-route-1"]["metadata"].update(
        {
            "approval_sent_to": ["approver-1"],
            "approval_requested_at": "2026-03-26T10:00:00+00:00",
        }
    )
    workflow = MagicMock()

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.services.finance_skills.ap_skill.resolve_slack_runtime") as resolve_runtime:
            resolve_runtime.return_value = {
                "connected": True,
                "approval_channel": "cl-finance-ap",
                "source": "shared_env",
            }
            with patch(
                "clearledgr.services.finance_skills.ap_skill.send_approval_reminder",
                AsyncMock(return_value=True),
            ) as send_reminder:
                result = asyncio.run(
                    runtime.execute_intent(
                        "nudge_approval",
                        {"email_id": "gmail-thread-route-1"},
                        idempotency_key="idem-runtime-nudge-approval-fallback-1",
                    )
                )

    assert result["status"] == "nudged"
    assert result["audit_event_id"]
    assert result["fallback"]["status"] == "sent"
    assert result["fallback"]["delivery"] == "approval_reminder_fallback"
    assert result["fallback"]["channel"] == "cl-finance-ap"
    assert result["fallback"]["slack_connected"] is True
    send_reminder.assert_awaited_once()
    kwargs = send_reminder.await_args.kwargs
    assert kwargs["organization_id"] == "default"
    assert kwargs["approver_ids"] == ["approver-1"]
    assert kwargs["stage"] == "reminder"
    assert kwargs["escalation_channel"] == "cl-finance-ap"
    assert kwargs["hours_pending"] >= 1.0


def test_execute_nudge_approval_returns_success_when_audit_append_fails():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    db.items["ap-route-1"]["metadata"].update(
        {
            "approval_sent_to": ["approver-1"],
            "approval_requested_at": "2026-03-26T10:00:00+00:00",
        }
    )
    workflow = MagicMock()

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.services.finance_skills.ap_skill.resolve_slack_runtime") as resolve_runtime:
            resolve_runtime.return_value = {
                "connected": True,
                "approval_channel": "cl-finance-ap",
                "source": "shared_env",
            }
            with patch(
                "clearledgr.services.finance_skills.ap_skill.send_approval_reminder",
                AsyncMock(return_value=True),
            ):
                with patch.object(runtime, "append_runtime_audit", side_effect=RuntimeError("audit_locked")):
                    result = asyncio.run(
                        runtime.execute_intent(
                            "nudge_approval",
                            {"email_id": "gmail-thread-route-1"},
                            idempotency_key="idem-runtime-nudge-approval-audit-failure-1",
                        )
                    )

    assert result["status"] == "nudged"
    assert result["fallback"]["status"] == "sent"
    assert result["audit_status"] == "error"
    assert result["audit_error"] == "audit_locked"


def test_execute_nudge_approval_reports_slack_not_connected():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    db.items["ap-route-1"]["metadata"].update(
        {
            "approval_sent_to": [],
            "approval_requested_at": "2026-03-26T10:00:00+00:00",
        }
    )
    workflow = MagicMock()

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch("clearledgr.services.finance_skills.ap_skill.resolve_slack_runtime") as resolve_runtime:
            resolve_runtime.return_value = {
                "connected": False,
                "approval_channel": "cl-finance-ap",
                "source": "shared_env_unconfigured",
            }
            with patch(
                "clearledgr.services.finance_skills.ap_skill.send_approval_reminder",
                AsyncMock(return_value=False),
            ):
                result = asyncio.run(
                    runtime.execute_intent(
                        "nudge_approval",
                        {"email_id": "gmail-thread-route-1"},
                        idempotency_key="idem-runtime-nudge-approval-fallback-unconfigured-1",
                    )
                )

    assert result["status"] == "error"
    assert result["audit_event_id"]
    assert result["fallback"]["status"] == "error"
    assert result["fallback"]["reason"] == "slack_not_connected"
    assert result["fallback"]["channel"] == "cl-finance-ap"
    assert result["fallback"]["slack_connected"] is False
    assert result["fallback"]["slack_source"] == "shared_env_unconfigured"


def test_execute_reassign_approval_updates_pending_approver_and_chain():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "needs_approval"
    db.items["ap-route-1"]["metadata"]["approval_chain_id"] = "chain-1"
    db.items["ap-route-1"]["metadata"]["approval_sent_to"] = ["old-approver"]
    db.slack_threads["gmail-thread-route-1"] = {"channel_id": "C123", "thread_ts": "171.99"}
    workflow = MagicMock()
    workflow.slack_client = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(channel="C123", thread_ts="171.99", ts="171.100"))
    )

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "reassign_approval",
                {
                    "email_id": "gmail-thread-route-1",
                    "assignee": "new-approver",
                },
                idempotency_key="idem-runtime-reassign-approval-1",
            )
        )

    assert result["status"] == "reassigned"
    assert result["assignee"] == "new-approver"
    assert result["audit_event_id"]
    assert db.items["ap-route-1"]["metadata"]["approval_sent_to"] == ["new-approver"]
    assert db.items["ap-route-1"]["metadata"]["approval_last_reassigned_to"] == "new-approver"
    assert db.reassigned_chains[-1]["chain_id"] == "chain-1"
    assert db.reassigned_chains[-1]["approvers"] == ["new-approver"]


def test_execute_route_low_risk_for_approval_blocks_autonomous_mode_when_vendor_not_earned():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }
    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        with patch.object(runtime, "skill_readiness", return_value=readiness):
            result = asyncio.run(
                runtime.execute_intent(
                    "route_low_risk_for_approval",
                    {
                        "email_id": "gmail-thread-route-1",
                        "execution_context": "autonomous",
                    },
                    idempotency_key="idem-runtime-route-autonomy-blocked-1",
                )
            )

    assert result["status"] == "blocked"
    assert result["reason"] == "autonomy_gate_blocked"
    assert result["policy_precheck"]["autonomy_policy"]["mode"] == "assisted"
    assert result["policy_precheck"]["autonomous_requested"] is True
    workflow._send_for_approval.assert_not_called()


def test_execute_post_to_erp_blocked_by_field_review_precheck():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-route-1"]["state"] = "ready_to_post"
    workflow = MagicMock()
    workflow.evaluate_financial_action_precheck.return_value = {
        "eligible": False,
        "reason_codes": ["field_review_required", "blocking_source_conflicts"],
        "state": "ready_to_post",
        "blocked_fields": ["amount"],
        "source_conflicts": [
            {
                "field": "amount",
                "blocking": True,
                "reason": "source_value_mismatch",
            }
        ],
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "post_to_erp",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-post-blocked-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"
    assert result["audit_event_id"]
    workflow.approve_invoice.assert_not_called()


def test_execute_prepare_vendor_followups_waiting_sla_block():
    db = _FakeDB()
    runtime = _runtime(db)
    db.items["ap-followup-1"]["metadata"]["followup_attempt_count"] = 1
    db.items["ap-followup-1"]["metadata"]["followup_last_sent_at"] = "2099-01-01T00:00:00+00:00"

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=MagicMock()):
        result = asyncio.run(
            runtime.execute_intent(
                "prepare_vendor_followups",
                {"email_id": "gmail-thread-followup-1"},
                idempotency_key="idem-runtime-followup-waiting-1",
            )
        )

    assert result["status"] == "waiting_sla"
    assert result["reason"] == "waiting_for_sla_window"
    assert result["audit_event_id"]
    assert result["followup_next_action"] == "await_vendor_response"


def test_execute_prepare_vendor_followups_prepares_draft_and_replays_idempotent_request():
    db = _FakeDB()
    runtime = _runtime(db)

    class _FakeGmailClient:
        def __init__(self, user_id):
            self.user_id = user_id

        async def ensure_authenticated(self):
            return True

    class _FakeFollowupService:
        def __init__(self, organization_id):
            self.organization_id = organization_id

        async def create_gmail_draft(self, **_kwargs):
            return "draft-runtime-followup-1"

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=MagicMock()):
        with patch("clearledgr.services.gmail_api.GmailAPIClient", _FakeGmailClient):
            with patch("clearledgr.services.auto_followup.AutoFollowUpService", _FakeFollowupService):
                first = asyncio.run(
                    runtime.execute_intent(
                        "prepare_vendor_followups",
                        {"email_id": "gmail-thread-followup-1"},
                        idempotency_key="idem-runtime-followup-1",
                    )
                )
                second = asyncio.run(
                    runtime.execute_intent(
                        "prepare_vendor_followups",
                        {"email_id": "gmail-thread-followup-1"},
                        idempotency_key="idem-runtime-followup-1",
                    )
                )

    assert first["status"] == "prepared"
    assert first["draft_id"] == "draft-runtime-followup-1"
    assert first["audit_event_id"]
    assert second["status"] == "prepared"
    assert second["idempotency_replayed"] is True
    assert db.items["ap-followup-1"]["metadata"]["needs_info_draft_id"] == "draft-runtime-followup-1"
    assert db.items["ap-followup-1"]["metadata"]["followup_next_action"] == "await_vendor_response"
    assert len(db.audit_rows) == 1


def test_execute_retry_recoverable_failures_blocked_by_precheck():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_retry_recoverable_failure.return_value = {
        "eligible": False,
        "reason_codes": ["non_recoverable_failure"],
        "recoverability": {"recoverable": False, "reason": "non_recoverable_failure"},
        "state": "failed_post",
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_intent(
                "retry_recoverable_failures",
                {"email_id": "gmail-thread-retry-1"},
                idempotency_key="idem-runtime-retry-blocked-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "retry_not_recoverable"
    assert result["audit_event_id"]


def test_execute_retry_recoverable_failures_success_and_replay():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_retry_recoverable_failure.return_value = {
        "eligible": True,
        "reason_codes": [],
        "recoverability": {"recoverable": True, "reason": "recoverable_timeout"},
        "state": "failed_post",
    }
    workflow.resume_workflow = AsyncMock(return_value={"status": "recovered", "erp_reference": "ERP-RUNTIME-1"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        first = asyncio.run(
            runtime.execute_intent(
                "retry_recoverable_failures",
                {"email_id": "gmail-thread-retry-1"},
                idempotency_key="idem-runtime-retry-1",
            )
        )
        second = asyncio.run(
            runtime.execute_intent(
                "retry_recoverable_failures",
                {"email_id": "gmail-thread-retry-1"},
                idempotency_key="idem-runtime-retry-1",
            )
        )

    assert first["status"] == "posted"
    assert first["erp_reference"] == "ERP-RUNTIME-1"
    assert first["audit_event_id"]
    assert second["status"] == "posted"
    assert second["idempotency_replayed"] is True
    assert len(db.audit_rows) == 1


def test_preview_retry_recoverable_failures_returns_precheck():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_retry_recoverable_failure.return_value = {
        "eligible": True,
        "reason_codes": [],
        "state": "failed_post",
    }

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        result = runtime.preview_intent(
            "retry_recoverable_failures",
            {"email_id": "gmail-thread-retry-1"},
        )

    assert result["intent"] == "retry_recoverable_failures"
    assert result["status"] == "eligible"
    assert result["policy_precheck"]["eligible"] is True


def test_preview_read_ap_workflow_health_returns_snapshot():
    db = _FakeDB()
    runtime = _runtime(db)

    result = runtime.preview_intent("read_ap_workflow_health", {"limit": 100})

    assert result["intent"] == "read_ap_workflow_health"
    assert result["status"] == "ready"
    assert result["policy_precheck"]["read_only"] is True
    assert result["summary"]["total_items"] >= 3
    assert result["summary"]["state_counts"]["validated"] >= 1


def test_preview_read_vendor_compliance_health_returns_snapshot():
    db = _FakeDB()
    runtime = _runtime(db)

    result = runtime.preview_intent("read_vendor_compliance_health", {"limit": 50})

    assert result["intent"] == "read_vendor_compliance_health"
    assert result["status"] == "ready"
    assert result["policy_precheck"]["read_only"] is True
    assert "summary" in result
    assert "high_override_vendors_count" in result["summary"]


def test_execute_read_ap_workflow_health_returns_read_only_snapshot():
    db = _FakeDB()
    runtime = _runtime(db)

    result = asyncio.run(runtime.execute_intent("read_ap_workflow_health", {"limit": 100}))

    assert result["intent"] == "read_ap_workflow_health"
    assert result["status"] == "snapshot_ready"
    assert result["read_only"] is True
    assert result["summary"]["total_items"] >= 3


def test_execute_read_vendor_compliance_health_returns_read_only_snapshot():
    db = _FakeDB()
    runtime = _runtime(db)

    result = asyncio.run(runtime.execute_intent("read_vendor_compliance_health", {"limit": 50}))

    assert result["intent"] == "read_vendor_compliance_health"
    assert result["status"] == "snapshot_ready"
    assert result["read_only"] is True
    assert "summary" in result


def test_runtime_preview_and_execute_include_canonical_contract_fields():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval", "slack_ts": "171.10"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        preview = runtime.preview_intent(
            "route_low_risk_for_approval",
            {"email_id": "gmail-thread-route-1"},
        )
        executed = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-contract-1",
            )
        )

    for payload in (preview, executed):
        assert "recommended_next_action" in payload
        assert "legal_actions" in payload
        assert "blockers" in payload
        assert "confidence" in payload
        assert "evidence_refs" in payload

    assert executed["action_execution"]["action"] == "route_low_risk_for_approval"
    assert executed["action_execution"]["idempotency_key"] == "idem-runtime-contract-1"


def test_runtime_audit_rows_include_canonical_audit_event_schema():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.evaluate_batch_route_low_risk_for_approval.return_value = {
        "eligible": True,
        "reason_codes": [],
    }
    workflow.build_invoice_data_from_ap_item.return_value = SimpleNamespace(gmail_id="gmail-thread-route-1")
    workflow._send_for_approval = AsyncMock(return_value={"status": "pending_approval"})

    with patch("clearledgr.services.finance_skills.ap_skill.get_invoice_workflow", return_value=workflow):
        _ = asyncio.run(
            runtime.execute_intent(
                "route_low_risk_for_approval",
                {"email_id": "gmail-thread-route-1"},
                idempotency_key="idem-runtime-audit-schema-1",
            )
        )

    assert db.audit_rows
    metadata = db.audit_rows[0].get("metadata") or {}
    canonical = metadata.get("canonical_audit_event") or {}
    assert canonical.get("org_id") == "default"
    assert canonical.get("entity_id") == "ap-route-1"
    assert canonical.get("action")
    assert canonical.get("timestamp")


def test_execute_ap_invoice_processing_fails_closed_when_workflow_unavailable():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.process_new_invoice = AsyncMock(side_effect=RuntimeError("workflow unavailable"))

    invoice_payload = {
        "gmail_id": "gmail-fail-closed-1",
        "thread_id": "gmail-thread-fail-closed-1",
        "message_id": "gmail-message-fail-closed-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-FAIL-1",
        "vendor_name": "Planner Down Co",
        "amount": 42.0,
        "currency": "USD",
    }

    with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_ap_invoice_processing(
                invoice_payload=invoice_payload,
                idempotency_key="idem-fail-closed-1",
                correlation_id="corr-fail-closed-1",
            )
        )

    assert result["status"] == "error"
    assert result["reason"] == "invoice_workflow_unavailable"
    assert result["execution_mode"] == "finance_agent_runtime"
    assert result["agent_status"] == "failed"
    assert result["idempotency_key"] == "idem-fail-closed-1"
    assert result["correlation_id"] == "corr-fail-closed-1"
    seeded = db.get_ap_item_by_thread("default", "gmail-thread-fail-closed-1")
    assert seeded is not None
    assert seeded["thread_id"] == "gmail-thread-fail-closed-1"
    assert seeded["message_id"] == "gmail-message-fail-closed-1"
    assert seeded["last_error"] == "workflow unavailable"
    assert seeded["metadata"]["exception_code"] == "workflow_execution_failed"
    assert seeded["metadata"]["workflow_error"] == "workflow unavailable"


def test_seed_ap_item_replaces_placeholder_vendor_and_zero_amount():
    db = _FakeDB()
    db.items["ap-placeholder-1"] = {
        "id": "ap-placeholder-1",
        "organization_id": "default",
        "thread_id": "gmail-thread-placeholder-1",
        "message_id": "gmail-message-placeholder-1",
        "state": "received",
        "vendor_name": "Unknown vendor",
        "invoice_number": None,
        "amount": 0.0,
        "currency": "USD",
        "subject": "Invoice",
        "sender": "billing@placeholder.test",
        "confidence": 0.1,
        "metadata": {},
    }
    runtime = _runtime(db)

    item = runtime._seed_ap_item_for_invoice_processing(
        {
            "organization_id": "default",
            "thread_id": "gmail-thread-placeholder-1",
            "message_id": "gmail-message-placeholder-1",
            "sender": "Google Payments <payments-noreply@google.com>",
            "subject": "Google Workspace invoice",
            "vendor_name": "Unknown",
            "amount": 123.45,
            "currency": "USD",
            "invoice_number": "5499678906",
            "confidence": 0.98,
        },
        correlation_id="corr-placeholder-1",
    )

    assert item is not None
    assert item["vendor_name"] == "Google Payments"
    assert item["amount"] == 123.45
    assert item["invoice_number"] == "5499678906"


def test_refresh_invoice_record_from_extraction_updates_ap_item_without_planner():
    db = _FakeDB()
    runtime = _runtime(db)

    result = runtime.refresh_invoice_record_from_extraction(
        {
            "organization_id": "default",
            "thread_id": "gmail-thread-refresh-1",
            "message_id": "gmail-message-refresh-1",
            "sender": "billing@vendor.test",
            "subject": "Invoice INV-REFRESH-1",
            "vendor_name": "Vendor Refresh Co",
            "amount": 451.23,
            "currency": "USD",
            "invoice_number": "INV-REFRESH-1",
            "primary_source": "attachment",
        },
        attachments=[{"filename": "invoice.pdf"}],
        correlation_id="corr-refresh-1",
        refresh_reason="golden_replay",
    )

    assert result["status"] == "refreshed"
    seeded = db.get_ap_item_by_thread("default", "gmail-thread-refresh-1")
    assert seeded is not None
    assert seeded["vendor_name"] == "Vendor Refresh Co"
    assert seeded["amount"] == 451.23
    assert seeded["invoice_number"] == "INV-REFRESH-1"
    assert seeded["metadata"]["processing_status"] == "extraction_refreshed"
    assert seeded["metadata"]["refresh_reason"] == "golden_replay"


def test_refresh_invoice_record_from_extraction_clears_stale_runtime_failure():
    db = _FakeDB()
    db.items["ap-stale-planner-1"] = {
        "id": "ap-stale-planner-1",
        "organization_id": "default",
        "thread_id": "gmail-thread-stale-planner-1",
        "message_id": "gmail-message-stale-planner-1",
        "state": "received",
        "vendor_name": "Stale Planner Co",
        "invoice_number": "INV-STALE-1",
        "amount": 99.0,
        "currency": "USD",
        "exception_code": "workflow_execution_failed",
        "exception_severity": "high",
        "last_error": "workflow unavailable",
        "metadata": {
            "exception_code": "workflow_execution_failed",
            "exception_severity": "high",
            "processing_status": "workflow_execution_failed",
            "planner_error": "APSkill not registered",
            "workflow_error": "workflow unavailable",
        },
    }
    runtime = _runtime(db)

    result = runtime.refresh_invoice_record_from_extraction(
        {
            "organization_id": "default",
            "thread_id": "gmail-thread-stale-planner-1",
            "message_id": "gmail-message-stale-planner-1",
            "sender": "billing@vendor.test",
            "subject": "Invoice INV-STALE-1",
            "vendor_name": "Stale Planner Co",
            "amount": 99.0,
            "currency": "USD",
            "invoice_number": "INV-STALE-1",
            "primary_source": "attachment",
        },
        attachments=[{"filename": "invoice.pdf"}],
        correlation_id="corr-stale-planner-1",
        refresh_reason="historical_repair",
    )

    assert result["status"] == "refreshed"
    seeded = db.get_ap_item("ap-stale-planner-1")
    assert seeded is not None
    assert seeded["exception_code"] is None
    assert seeded["exception_severity"] is None


def test_refresh_invoice_record_from_extraction_overwrites_stale_existing_fields_on_repair():
    db = _FakeDB()
    db.items["ap-stale-refresh-1"] = {
        "id": "ap-stale-refresh-1",
        "organization_id": "default",
        "thread_id": "gmail-thread-stale-refresh-1",
        "message_id": "gmail-message-stale-refresh-1",
        "state": "received",
        "vendor_name": "F mae ia",
        "invoice_number": "000127",
        "amount": 0.0,
        "currency": "USD",
        "due_date": None,
        "subject": "Old subject",
        "sender": "old@example.test",
        "metadata": {},
    }
    runtime = _runtime(db)

    result = runtime.refresh_invoice_record_from_extraction(
        {
            "organization_id": "default",
            "thread_id": "gmail-thread-stale-refresh-1",
            "message_id": "gmail-message-stale-refresh-1",
            "sender": "Mo Mbalam <israelmbalam@gmail.com>",
            "subject": "Re: Tuition fees from Little Learners Nursery and Preschool",
            "vendor_name": "Little learners nursery and preschool",
            "amount": 5000.0,
            "currency": "GHS",
            "invoice_number": "000127",
            "due_date": "2026-04-16",
            "primary_source": "attachment",
            "intake_source": "gmail_replay_refresh",
            "field_confidences": {"vendor": 0.94, "amount": 0.95, "invoice_number": 0.94, "due_date": 0.89},
        },
        attachments=[{"filename": "invoice.pdf"}],
        correlation_id="corr-stale-refresh-1",
        refresh_reason="historical_repair_pass",
    )

    assert result["status"] == "refreshed"
    seeded = db.get_ap_item("ap-stale-refresh-1")
    assert seeded is not None
    assert seeded["vendor_name"] == "Little learners nursery and preschool"
    assert seeded["amount"] == 5000.0
    assert seeded["currency"] == "GHS"
    assert seeded["due_date"] == "2026-04-16"
    assert seeded["metadata"]["processing_status"] == "extraction_refreshed"


def test_execute_ap_invoice_processing_invokes_invoice_workflow_directly():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.process_new_invoice = AsyncMock(
        return_value={"status": "processed", "ap_item_state": "validated"}
    )

    invoice_payload = {
        "gmail_id": "gmail-skill-register-1",
        "thread_id": "gmail-thread-skill-register-1",
        "message_id": "gmail-message-skill-register-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-SKILL-1",
        "vendor_name": "Planner Registration Co",
        "amount": 42.0,
        "currency": "USD",
    }

    with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_ap_invoice_processing(
                invoice_payload=invoice_payload,
                idempotency_key="idem-skill-register-1",
                correlation_id="corr-skill-register-1",
            )
        )

    workflow.process_new_invoice.assert_awaited_once()
    invoice_data = workflow.process_new_invoice.await_args.args[0]
    assert invoice_data.gmail_id == "gmail-thread-skill-register-1"
    assert result["status"] == "processed"
    assert result["execution_mode"] == "finance_agent_runtime"
    assert result["agent_status"] == "completed"


def test_execute_ap_invoice_processing_records_finance_learning_outcome():
    db = _FakeDB()
    runtime = _runtime(db)
    workflow = MagicMock()
    workflow.process_new_invoice = AsyncMock(
        return_value={
            "status": "processed",
            "ap_item_state": "validated",
            "gl_code": "2000",
            "gl_description": "Accounts Payable",
        }
    )
    captured = {}

    class _FakeFinanceLearning:
        def record_runtime_outcome(self, **kwargs):
            captured["record_kwargs"] = dict(kwargs or {})
            return {"recorded": ["vendor_gl_approval"]}

    invoice_payload = {
        "gmail_id": "gmail-learning-1",
        "thread_id": "gmail-thread-learning-1",
        "message_id": "gmail-message-learning-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-LEARN-1",
        "vendor_name": "Learning Co",
        "amount": 42.0,
        "currency": "USD",
    }

    with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=workflow):
        with patch(
            "clearledgr.services.finance_learning.get_finance_learning_service",
            return_value=_FakeFinanceLearning(),
        ):
            result = asyncio.run(
                runtime.execute_ap_invoice_processing(
                    invoice_payload=invoice_payload,
                    idempotency_key="idem-learning-1",
                    correlation_id="corr-learning-1",
                )
            )

    assert result["status"] == "processed"
    assert captured["record_kwargs"]["response"]["status"] == "processed"
    assert captured["record_kwargs"]["shadow_decision"]["proposed_action"] == "route_for_approval"
    assert captured["record_kwargs"]["ap_item"]["id"].startswith("ap-created-")


def test_execute_ap_invoice_processing_downgrades_auto_post_when_autonomy_not_earned():
    db = _FakeDB()
    runtime = _runtime(db)
    captured = {}
    workflow = MagicMock()

    async def _process_new_invoice(invoice_data):
        captured["invoice"] = invoice_data
        return {"status": "pending_approval"}

    workflow.process_new_invoice = AsyncMock(side_effect=_process_new_invoice)

    readiness = {
        "status": "ready",
        "gates": [],
        "metrics": {"ap_kpis": db.get_ap_kpis("default")},
    }
    invoice_payload = {
        "gmail_id": "gmail-autonomy-downgrade-1",
        "thread_id": "gmail-thread-autonomy-downgrade-1",
        "message_id": "gmail-message-autonomy-downgrade-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-AUTO-1",
        "vendor_name": "Unscored Vendor Co",
        "amount": 42.0,
        "currency": "USD",
        "confidence": 0.99,
    }

    with patch.object(runtime, "skill_readiness", return_value=readiness):
        with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=workflow):
            result = asyncio.run(
                runtime.execute_ap_invoice_processing(
                    invoice_payload=invoice_payload,
                    idempotency_key="idem-autonomy-downgrade-1",
                    correlation_id="corr-autonomy-downgrade-1",
                )
            )

    threshold = runtime.ap_auto_approve_threshold()
    task_invoice = captured["invoice"]
    assert float(task_invoice.confidence) < threshold
    assert result["status"] == "pending_approval"
    assert result["autonomy_policy"]["mode"] == "assisted"
    assert result["autonomy_auto_post_downgraded"] is True
    seeded = db.get_ap_item_by_thread("default", "gmail-thread-autonomy-downgrade-1")
    assert seeded is not None
    assert seeded["metadata"]["autonomy_mode"] == "assisted"


def test_execute_ap_invoice_processing_blocks_on_field_review_without_planner():
    db = _FakeDB()
    runtime = _runtime(db)

    workflow = MagicMock()
    workflow.process_new_invoice = AsyncMock()
    invoice_payload = {
        "gmail_id": "gmail-blocked-1",
        "thread_id": "gmail-thread-blocked-1",
        "message_id": "gmail-message-blocked-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-BLOCK-1",
        "vendor_name": "Conflict Co",
        "amount": 199.0,
        "currency": "USD",
        "invoice_number": "INV-BLOCK-1",
        "requires_field_review": True,
        "confidence_blockers": [{"field": "amount", "reason": "source_value_mismatch"}],
        "source_conflicts": [
            {
                "field": "amount",
                "blocking": True,
                "reason": "source_value_mismatch",
                "preferred_source": "attachment",
                "values": {"email": 0.0, "attachment": 199.0},
            }
        ],
        "conflict_actions": [{"action": "review_fields", "field": "amount", "blocking": True}],
        "field_confidences": {"amount": 0.98},
    }

    with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=workflow):
        result = asyncio.run(
            runtime.execute_ap_invoice_processing(
                invoice_payload=invoice_payload,
                idempotency_key="idem-blocked-1",
                correlation_id="corr-blocked-1",
            )
        )

    assert result["status"] == "blocked"
    assert result["reason"] == "field_review_required"
    workflow.process_new_invoice.assert_not_awaited()
    seeded = db.get_ap_item_by_thread("default", "gmail-thread-blocked-1")
    assert seeded is not None
    assert seeded["exception_code"] == "field_conflict"
    assert seeded["metadata"]["requires_field_review"] is True
    assert seeded["metadata"]["processing_status"] == "field_review_required"


def test_runtime_prefers_sender_name_over_generic_processor_vendor():
    db = _FakeDB()
    runtime = _runtime(db)

    vendor = runtime._resolved_vendor_name(
        "Stripe",
        "Replit <invoice+statements+acct_15YpNsJAmnYVOvfn@stripe.com>",
    )

    assert vendor == "Replit"


def test_ap_auto_approve_threshold_reads_org_settings():
    db = _FakeDB()
    runtime = _runtime(db)

    assert runtime.ap_auto_approve_threshold() == 0.91


def test_escalate_invoice_review_appends_runtime_audit():
    db = _FakeDB()
    runtime = _runtime(db)

    async def _fake_send(payload):
        return {
            "status": "sent",
            "delivered": True,
            "channel": payload.get("channel"),
            "email_id": payload.get("email_id"),
        }

    with patch(
        "clearledgr.workflows.gmail_activities.send_slack_notification_activity",
        _fake_send,
    ):
        result = asyncio.run(
            runtime.escalate_invoice_review(
                email_id="gmail-thread-route-1",
                vendor="Runtime Co",
                amount=123.45,
                currency="USD",
                confidence=82.0,
                mismatches=[{"message": "Amount mismatch"}],
                channel="#finance-escalations",
            )
        )

    assert result["status"] == "escalated"
    assert result["audit_event_id"]
    assert db.audit_rows[-1]["event_type"] == "invoice_escalated"
    assert db.audit_rows[-1]["metadata"]["delivery"]["status"] == "sent"


def test_record_field_correction_appends_runtime_audit():
    db = _FakeDB()
    runtime = _runtime(db)
    captured = {}

    class _FakeFinanceLearning:
        def record_manual_field_correction(self, **kwargs):
            captured["record_kwargs"] = dict(kwargs or {})
            return {"correction_learning": {"stored": True}}

    with patch(
        "clearledgr.services.finance_learning.get_finance_learning_service",
        return_value=_FakeFinanceLearning(),
    ):
        result = runtime.record_field_correction(
            ap_item_id="ap-route-1",
            field="invoice_number",
            original_value="INV-OLD",
            corrected_value="INV-NEW",
            feedback="Corrected from source email",
        )

    assert result["status"] == "recorded"
    assert result["audit_event_id"]
    assert captured["record_kwargs"]["field"] == "invoice_number"
    assert captured["record_kwargs"]["context"]["selected_source"] == "manual"
    assert db.audit_rows[-1]["event_type"] == "field_correction"


def test_append_runtime_audit_syncs_agent_memory_when_service_available():
    db = _FakeDB()
    runtime = _runtime(db)
    captured = {}

    class _FakeMemory:
        def observe_event(self, **kwargs):
            captured["event_kwargs"] = dict(kwargs or {})
            return {"id": "mem-1"}

        def capture_runtime_state(self, **kwargs):
            captured["state_kwargs"] = dict(kwargs or {})
            return {"belief_state": {"ap_item_id": kwargs.get("ap_item_id")}}

    with patch(
        "clearledgr.services.agent_memory.get_agent_memory_service",
        return_value=_FakeMemory(),
    ):
        audit_row = runtime.append_runtime_audit(
            ap_item_id="ap-route-1",
            event_type="ap_invoice_processing_completed",
            reason="ap_invoice_processing_processed",
            metadata={"response": {"status": "processed"}},
            correlation_id="corr-memory-1",
            skill_id="ap_v1",
        )

    assert audit_row is not None
    assert captured["event_kwargs"]["ap_item_id"] == "ap-route-1"
    assert captured["event_kwargs"]["summary"] == "ap_invoice_processing_processed"
    assert captured["state_kwargs"]["response"]["status"] == "processed"


def test_append_runtime_audit_syncs_finance_learning_when_service_available():
    db = _FakeDB()
    runtime = _runtime(db)
    captured = {}

    class _FakeLearning:
        def record_action_outcome(self, **kwargs):
            captured["learning_kwargs"] = dict(kwargs or {})
            return {"event": {"id": "learning-1"}}

    with patch(
        "clearledgr.services.finance_learning.get_finance_learning_service",
        return_value=_FakeLearning(),
    ):
        audit_row = runtime.append_runtime_audit(
            ap_item_id="ap-route-1",
            event_type="approval_request_routed",
            reason="approval_request_sent",
            metadata={"response": {"status": "pending_approval", "email_id": "gmail-thread-route-1"}},
            correlation_id="corr-learning-sync-1",
            skill_id="ap_v1",
        )

    assert audit_row is not None
    assert captured["learning_kwargs"]["event_type"] == "approval_request_routed"
    assert captured["learning_kwargs"]["response"]["audit_event_id"] == audit_row["id"]
    assert captured["learning_kwargs"]["metadata"]["reason"] == "approval_request_sent"
    assert captured["learning_kwargs"]["ap_item"]["id"] == "ap-route-1"


def test_execute_skill_request_routes_through_agent_loop_owner():
    db = _FakeDB()
    runtime = _runtime(db)
    request = runtime._build_skill_request(
        intent="route_low_risk_for_approval",
        payload={"email_id": "gmail-thread-route-1"},
    )
    captured = {}

    fake_skill_response = SimpleNamespace(
        to_dict=lambda: {"status": "pending_approval", "next_step": "await_approval"}
    )
    fake_skill = SimpleNamespace(
        skill_id="ap_v1",
        execute_contract=AsyncMock(return_value=fake_skill_response),
    )

    class _FakeLoop:
        async def run_skill_request(self, request_arg, action_arg, executor):
            captured["request"] = request_arg
            captured["action"] = action_arg
            response = await executor()
            response["agent_loop"] = {"owner": "fake_loop", "observed": True}
            return response

    with patch.object(runtime, "_skill_for_intent", return_value=fake_skill):
        with patch.object(runtime, "_agent_loop_service", return_value=_FakeLoop()):
            result = asyncio.run(runtime.execute_skill_request(request))

    assert result["status"] == "pending_approval"
    assert result["agent_loop"]["owner"] == "fake_loop"
    assert captured["request"].task_type == "route_low_risk_for_approval"
    assert captured["action"].action == "route_low_risk_for_approval"
    fake_skill.execute_contract.assert_awaited_once()


def test_finance_lead_summary_prefers_agent_memory_next_action_label():
    db = _FakeDB()
    runtime = _runtime(db)
    ap_item = dict(db.items["ap-route-1"])

    class _FakeMemory:
        def build_surface(self, *, ap_item_id: str, skill_id: str = "ap_v1") -> dict:
            assert ap_item_id == "ap-route-1"
            assert skill_id == "ap_v1"
            return {
                "profile": {"name": "Clearledgr AP Agent"},
                "belief": {"reason": "Approval is pending with the assigned approver."},
                "current_state": "validated",
                "status": "pending_approval",
                "evidence": {},
                "uncertainties": {},
                "next_action": {"type": "await_approval", "label": "Wait for approval decision"},
                "summary": {"reason": "Approval is pending with the assigned approver."},
                "episode": {"status": "pending_approval"},
            }

    with patch(
        "clearledgr.services.agent_memory.get_agent_memory_service",
        return_value=_FakeMemory(),
    ):
        payload = runtime._build_finance_lead_summary_payload(ap_item)

    assert payload["next_action"] == "await_approval"
    assert payload["agent_next_action"]["label"] == "Wait for approval decision"
    assert any("Wait for approval decision" in line for line in payload["lines"])
    assert any("Agent belief: Approval is pending with the assigned approver." in line for line in payload["lines"])
