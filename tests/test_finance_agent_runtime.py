"""Tests for the finance agent runtime contract (preview/execute)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class _FakeDB:
    def __init__(self) -> None:
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
    assert "read_ap_workflow_health" in runtime.supported_intents


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


def test_execute_read_ap_workflow_health_returns_read_only_snapshot():
    db = _FakeDB()
    runtime = _runtime(db)

    result = asyncio.run(runtime.execute_intent("read_ap_workflow_health", {"limit": 100}))

    assert result["intent"] == "read_ap_workflow_health"
    assert result["status"] == "snapshot_ready"
    assert result["read_only"] is True
    assert result["summary"]["total_items"] >= 3
