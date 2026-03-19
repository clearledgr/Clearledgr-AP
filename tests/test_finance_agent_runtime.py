"""Tests for the finance agent runtime contract (preview/execute)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class _FakeDB:
    def __init__(self) -> None:
        self.organization = {"id": "default", "settings": {"auto_approve_threshold": 0.91}}
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
        return None

    def get_ap_item_by_message_id(self, organization_id, message_id):
        org = str(organization_id or "")
        token = str(message_id or "")
        for item in self._all_items():
            if str(item.get("organization_id") or "") != org:
                continue
            if token == str(item.get("message_id") or ""):
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

    def get_ap_audit_event_by_key(self, idempotency_key):
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        for row in self.audit_rows:
            if str(row.get("idempotency_key") or "").strip() == key:
                return row
        return None

    def append_ap_audit_event(self, payload):
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
        return {
            "organization_id": organization_id,
            "agentic_telemetry": {
                "agent_suggestion_acceptance": {
                    "prompted_count": 10,
                    "accepted_count": 8,
                    "rate": 0.8,
                }
            },
        }

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
    assert second["status"] == "pending_approval"
    assert second["idempotency_replayed"] is True
    assert len(db.audit_rows) == 1


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


def test_execute_ap_invoice_processing_fails_closed_when_planner_unavailable():
    db = _FakeDB()
    runtime = _runtime(db)

    invoice_payload = {
        "gmail_id": "gmail-fail-closed-1",
        "organization_id": "default",
        "sender": "billing@example.com",
        "subject": "Invoice INV-FAIL-1",
        "vendor_name": "Planner Down Co",
        "amount": 42.0,
        "currency": "USD",
    }

    with patch(
        "clearledgr.core.agent_runtime.get_planning_engine",
        side_effect=RuntimeError("planner unavailable"),
    ):
        result = asyncio.run(
            runtime.execute_ap_invoice_processing(
                invoice_payload=invoice_payload,
                idempotency_key="idem-fail-closed-1",
                correlation_id="corr-fail-closed-1",
            )
        )

    assert result["status"] == "error"
    assert result["reason"] == "planning_engine_unavailable"
    assert result["execution_mode"] == "agent_planning_engine"
    assert result["agent_status"] == "failed"
    assert result["idempotency_key"] == "idem-fail-closed-1"
    assert result["correlation_id"] == "corr-fail-closed-1"


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

    class _FakeLearningService:
        def __init__(self, organization_id):
            captured["organization_id"] = organization_id

        def record_correction(self, **kwargs):
            captured["record_kwargs"] = dict(kwargs or {})
            return {"stored": True}

    class _FakeAuditTrail:
        def __init__(self):
            self.events = []

        def record_event(self, **kwargs):
            self.events.append(dict(kwargs or {}))

    fake_audit = _FakeAuditTrail()

    with patch(
        "clearledgr.services.correction_learning.CorrectionLearningService",
        _FakeLearningService,
    ):
        with patch(
            "clearledgr.services.audit_trail.get_audit_trail",
            return_value=fake_audit,
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
    assert captured["organization_id"] == "default"
    assert captured["record_kwargs"]["correction_type"] == "invoice_number"
    assert fake_audit.events[-1]["event_type"] == "field_correction"
    assert db.audit_rows[-1]["event_type"] == "field_correction"
