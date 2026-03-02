"""Durable ERP retry scheduling for AgentOrchestrator (GA R07).

These tests prove retries are persisted in the DB and can be resumed by a
fresh orchestrator instance after a process restart.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.services import agent_orchestrator as orchestrator_module
from clearledgr.services.agent_orchestrator import AgentOrchestrator
from clearledgr.services.invoice_workflow import InvoiceData


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "agent-retry.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_RETRY_BACKOFF_SECONDS", "0,0,0")
    monkeypatch.setenv("AP_AGENT_RETRY_POLL_SECONDS", "1")
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_MAX_ATTEMPTS", "3")
    db_module._DB_INSTANCE = None
    orchestrator_module._orchestrator_cache.clear()
    db = db_module.get_db()
    db.initialize()
    return db


def _create_failed_post_item(db, *, gmail_id: str = "thread-retry-1", correlation_id: str = "corr-retry-1"):
    item = db.create_ap_item(
        {
            "invoice_key": f"vendor|{gmail_id}|100.00|",
            "thread_id": gmail_id,
            "message_id": f"msg-{gmail_id}",
            "subject": f"Invoice {gmail_id}",
            "sender": "billing@example.com",
            "vendor_name": "Retry Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{gmail_id.upper()}",
            "due_date": "2026-03-10",
            "state": "validated",
            "confidence": 0.98,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-1",
            "metadata": {"correlation_id": correlation_id},
        }
    )
    for state in ("needs_approval", "approved", "ready_to_post", "failed_post"):
        assert db.update_ap_item(
            item["id"],
            state=state,
            _actor_type="test",
            _actor_id="test",
            _correlation_id=correlation_id,
        )
    return db.get_ap_item(item["id"])


def _create_validated_item(db, *, gmail_id: str = "thread-post-process-1", correlation_id: str = "corr-post-process-1"):
    return db.create_ap_item(
        {
            "invoice_key": f"vendor|{gmail_id}|100.00|",
            "thread_id": gmail_id,
            "message_id": f"msg-{gmail_id}",
            "subject": f"Invoice {gmail_id}",
            "sender": "billing@example.com",
            "vendor_name": "PostProcess Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{gmail_id.upper()}",
            "due_date": "2026-03-10",
            "state": "validated",
            "confidence": 0.98,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "user-1",
            "metadata": {"correlation_id": correlation_id},
        }
    )


def _invoice_from_item(item):
    return InvoiceData(
        gmail_id=item["thread_id"],
        subject=item.get("subject") or "",
        sender=item.get("sender") or "",
        vendor_name=item.get("vendor_name") or "Unknown",
        amount=float(item.get("amount") or 0),
        currency=item.get("currency") or "USD",
        invoice_number=item.get("invoice_number"),
        due_date=item.get("due_date"),
        organization_id=item.get("organization_id") or "default",
        confidence=float(item.get("confidence") or 0.0),
        correlation_id=(item.get("metadata") or {}).get("correlation_id")
        if isinstance(item.get("metadata"), dict)
        else None,
    )


def _new_orchestrator(db):
    orch = AgentOrchestrator("default")
    orch.workflow.db = db
    return orch


def _minimal_invoice() -> InvoiceData:
    return InvoiceData(
        gmail_id="thread-agentic-1",
        subject="Invoice INV-AGENTIC-1",
        sender="billing@example.com",
        vendor_name="Agentic Vendor",
        amount=99.5,
        currency="USD",
        invoice_number="INV-AGENTIC-1",
        due_date="2026-03-15",
        organization_id="default",
        confidence=0.98,
        correlation_id="corr-agentic-1",
    )


def test_process_invoice_defaults_to_agentic_runtime(monkeypatch):
    monkeypatch.delenv("AGENT_PLANNING_LOOP", raising=False)
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(return_value={"status": "completed", "task_run_id": "task-1"}),
    ):
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "completed"


def test_process_invoice_returns_failure_when_agentic_runtime_fails_and_legacy_fallback_disabled(monkeypatch):
    monkeypatch.delenv("AGENT_PLANNING_LOOP", raising=False)
    monkeypatch.setenv("AGENT_LEGACY_FALLBACK_ON_ERROR", "false")
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(side_effect=RuntimeError("anthropic_api_key_missing")),
    ):
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "failed"
    assert result["reason"] == "agent_runtime_failed"


def test_process_invoice_ignores_explicit_legacy_fallback_when_agentic_runtime_fails(monkeypatch):
    monkeypatch.delenv("AGENT_PLANNING_LOOP", raising=False)
    monkeypatch.setenv("AGENT_LEGACY_FALLBACK_ON_ERROR", "true")
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(side_effect=RuntimeError("anthropic_api_key_missing")),
    ):
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "failed"
    assert result["reason"] == "agent_runtime_failed"
    contract = result.get("runtime_contract") or {}
    assert contract.get("legacy_fallback_requested") is True
    assert contract.get("legacy_fallback_on_error") is False
    assert "legacy_fallback_opt_in_ignored" in (contract.get("warnings") or [])


def test_process_invoice_ignores_explicit_agentic_opt_out(monkeypatch):
    monkeypatch.setenv("AGENT_PLANNING_LOOP", "false")
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(return_value={"status": "completed", "task_run_id": "task-2"}),
    ) as runtime_mock:
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "completed"
    contract = result.get("runtime_contract") or {}
    assert contract.get("planning_loop_requested") is False
    assert contract.get("planning_loop_enabled") is True
    assert "planning_loop_opt_out_ignored" in (contract.get("warnings") or [])
    runtime_mock.assert_awaited_once()


def test_process_invoice_fails_closed_when_runtime_returns_non_dict(monkeypatch):
    monkeypatch.delenv("AGENT_PLANNING_LOOP", raising=False)
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(return_value=["unexpected-shape"]),
    ):
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "failed"
    assert result["reason"] == "agent_runtime_invalid_response"
    assert result["execution_path"] == "agentic_runtime"


def test_process_invoice_forces_agentic_mode_in_production_when_opt_out_requested(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_PLANNING_LOOP", "false")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(return_value={"status": "completed", "task_run_id": "task-prod-1"}),
    ) as runtime_mock:
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "completed"
    assert result.get("execution_path") == "agentic_runtime"
    contract = result.get("runtime_contract") or {}
    assert contract.get("production_env") is True
    assert contract.get("planning_loop_requested") is False
    assert contract.get("planning_loop_enabled") is True
    assert "planning_loop_forced_on_in_production" in (contract.get("warnings") or [])
    runtime_mock.assert_awaited_once()


def test_process_invoice_ignores_legacy_fallback_flag_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("AGENT_PLANNING_LOOP", raising=False)
    monkeypatch.setenv("AGENT_LEGACY_FALLBACK_ON_ERROR", "true")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
    orch = AgentOrchestrator("default")
    invoice = _minimal_invoice()

    with patch(
        "clearledgr.services.finance_agent_runtime.FinanceAgentRuntime.execute_ap_invoice_processing",
        new=AsyncMock(side_effect=RuntimeError("planner_failed")),
    ):
        result = asyncio.run(orch.process_invoice(invoice))

    assert result["status"] == "failed"
    assert result["reason"] == "agent_runtime_failed"
    contract = result.get("runtime_contract") or {}
    assert contract.get("production_env") is True
    assert contract.get("legacy_fallback_requested") is True
    assert contract.get("legacy_fallback_on_error") is False
    assert "legacy_fallback_forced_off_in_production" in (contract.get("warnings") or [])


def test_runtime_status_exposes_execution_contract(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_PLANNING_LOOP", "false")
    monkeypatch.setenv("AGENT_LEGACY_FALLBACK_ON_ERROR", "true")
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret")
    orch = AgentOrchestrator("default")

    status = orch.runtime_status()
    contract = status.get("execution_contract") or {}

    assert contract.get("production_env") is True
    assert contract.get("planning_loop_requested") is False
    assert contract.get("planning_loop_enabled") is True
    assert contract.get("legacy_fallback_requested") is True
    assert contract.get("legacy_fallback_on_error") is False
    assert "planning_loop_forced_on_in_production" in (contract.get("warnings") or [])
    assert "legacy_fallback_forced_off_in_production" in (contract.get("warnings") or [])


def test_durable_retry_runtime_status_reports_db_queue(monkeypatch):
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_RETRY_BACKOFF_SECONDS", "1,2,3")
    monkeypatch.setenv("AP_AGENT_RETRY_POLL_SECONDS", "4")
    orch = AgentOrchestrator("default")
    status = orch.autonomous_retry_runtime_status()
    assert status["enabled"] is True
    assert status["durable"] is True
    assert status["mode"] == "durable_db_retry_queue"
    assert status["backoff_seconds"] == [1, 2, 3]
    assert status["poll_interval_seconds"] == 4


def test_durable_post_process_runtime_status_reports_db_queue(monkeypatch):
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_DURABLE_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_BACKOFF_SECONDS", "2,6,10")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_MAX_ATTEMPTS", "4")
    orch = AgentOrchestrator("default")
    status = orch.post_process_runtime_status()
    assert status["enabled"] is True
    assert status["durable"] is True
    assert status["mode"] == "durable_db_post_process_queue"
    assert status["backoff_seconds"] == [2, 6, 10]
    assert status["max_attempts"] == 4


def test_post_process_runtime_status_disables_non_durable_fallback_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    orch = AgentOrchestrator("default")
    status = orch.post_process_runtime_status()
    assert status["allow_non_durable"] is False


def test_orchestrator_exposes_no_legacy_invoice_path():
    orch = AgentOrchestrator("default")
    assert not hasattr(orch, "_process_invoice_legacy")


def test_durable_retry_job_survives_restart_and_posts_to_erp(db, monkeypatch):
    item = _create_failed_post_item(db, gmail_id="thread-retry-success")
    invoice = _invoice_from_item(item)

    orch1 = _new_orchestrator(db)
    job = orch1._enqueue_erp_retry_job(
        invoice,
        {
            "status": "error",
            "erp_result": {"status": "error", "error_code": "timeout"},
        },
    )
    assert job is not None
    assert job["status"] == "pending"

    queued = db.list_agent_retry_jobs("default", ap_item_id=item["id"])
    assert queued
    assert queued[0]["status"] == "pending"

    # "Restart": new orchestrator instance processes the persisted job.
    orch2 = _new_orchestrator(db)

    async def _post_success(_invoice, idempotency_key=None, correlation_id=None):
        assert idempotency_key
        assert correlation_id == "corr-retry-1"
        return {
            "status": "success",
            "erp_reference": "ERP-RETRY-001",
            "bill_id": "BILL-001",
        }

    monkeypatch.setattr(orch2.workflow, "_post_to_erp", _post_success)
    summary = asyncio.run(orch2.process_due_retry_jobs(limit=10))
    assert summary["claimed"] == 1
    assert summary["succeeded"] == 1

    updated = db.get_ap_item(item["id"])
    assert updated["state"] == "posted_to_erp"
    assert updated["erp_reference"] == "ERP-RETRY-001"

    jobs = db.list_agent_retry_jobs("default", ap_item_id=item["id"])
    assert jobs[0]["status"] == "completed"
    assert int(jobs[0]["retry_count"]) == 1

    audits = db.list_ap_audit_events(item["id"])
    event_types = [a.get("event_type") for a in audits]
    assert "agent_retry_scheduled" in event_types
    assert "agent_retry_succeeded" in event_types
    transition_events = [a for a in audits if a.get("event_type") == "state_transition"]
    assert transition_events
    assert all(a.get("correlation_id") == "corr-retry-1" for a in transition_events if a.get("correlation_id"))


def test_durable_retry_reschedules_then_dead_letters_after_max_attempts(db, monkeypatch):
    monkeypatch.setenv("AP_AGENT_AUTONOMOUS_RETRY_MAX_ATTEMPTS", "2")
    item = _create_failed_post_item(db, gmail_id="thread-retry-fail", correlation_id="corr-retry-dead")
    invoice = _invoice_from_item(item)

    orch = _new_orchestrator(db)
    job = orch._enqueue_erp_retry_job(
        invoice,
        {
            "status": "error",
            "erp_result": {"status": "error", "error_code": "connector_auth_expired"},
        },
    )
    assert job is not None

    async def _post_fail(_invoice, idempotency_key=None, correlation_id=None):
        return {
            "status": "error",
            "error_code": "connector_auth_expired",
            "error_message": "Connector authentication expired",
        }

    monkeypatch.setattr(orch.workflow, "_post_to_erp", _post_fail)

    first = asyncio.run(orch.process_due_retry_jobs(limit=10))
    assert first["claimed"] == 1
    assert first["rescheduled"] == 1

    jobs_after_first = db.list_agent_retry_jobs("default", ap_item_id=item["id"])
    assert jobs_after_first[0]["status"] == "pending"
    assert int(jobs_after_first[0]["retry_count"]) == 1
    next_retry_at = jobs_after_first[0]["next_retry_at"]
    assert isinstance(next_retry_at, str)

    # Force due immediately to simulate time passing/restart.
    db.reschedule_agent_retry_job(
        jobs_after_first[0]["id"],
        next_retry_at=datetime.now(timezone.utc).isoformat(),
        last_error=jobs_after_first[0].get("last_error"),
        result=jobs_after_first[0].get("result") or {},
        status="pending",
    )

    orch_restart = _new_orchestrator(db)
    monkeypatch.setattr(orch_restart.workflow, "_post_to_erp", _post_fail)
    second = asyncio.run(orch_restart.process_due_retry_jobs(limit=10))
    assert second["claimed"] == 1
    assert second["dead_letter"] == 1

    final_item = db.get_ap_item(item["id"])
    assert final_item["state"] == "failed_post"
    assert "Connector authentication expired" in str(final_item.get("last_error") or "")

    final_jobs = db.list_agent_retry_jobs("default", ap_item_id=item["id"])
    assert final_jobs[0]["status"] == "dead_letter"
    assert int(final_jobs[0]["retry_count"]) == 2

    audits = db.list_ap_audit_events(item["id"])
    event_types = [a.get("event_type") for a in audits]
    assert "agent_retry_rescheduled" in event_types
    assert "agent_retry_dead_letter" in event_types


def test_durable_post_process_job_survives_restart_and_completes(db, monkeypatch):
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_DURABLE_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_BACKOFF_SECONDS", "0,0,0")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_MAX_ATTEMPTS", "3")
    item = _create_validated_item(db, gmail_id="thread-post-process-success")
    invoice = _invoice_from_item(item)

    orch1 = _new_orchestrator(db)
    job = orch1._enqueue_post_process_job(
        invoice,
        agent_decision=None,
        workflow_result={"status": "approved", "erp_status": "success"},
    )
    assert job is not None
    assert job["status"] == "pending"
    assert job["job_type"] == "post_process"

    queued = [j for j in db.list_agent_retry_jobs("default", ap_item_id=item["id"]) if j.get("job_type") == "post_process"]
    assert queued and queued[0]["status"] == "pending"

    orch2 = _new_orchestrator(db)
    monkeypatch.setattr(orch2, "_run_post_process_steps", AsyncMock(return_value=None))
    summary = asyncio.run(orch2.process_due_post_process_jobs(limit=10))
    assert summary["claimed"] == 1
    assert summary["succeeded"] == 1

    jobs = [j for j in db.list_agent_retry_jobs("default", ap_item_id=item["id"]) if j.get("job_type") == "post_process"]
    assert jobs[0]["status"] == "completed"
    assert int(jobs[0]["retry_count"]) == 1

    audits = db.list_ap_audit_events(item["id"])
    event_types = [a.get("event_type") for a in audits]
    assert "agent_post_process_enqueued" in event_types
    assert "agent_post_process_completed" in event_types


def test_durable_post_process_reschedules_then_dead_letters_after_max_attempts(db, monkeypatch):
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_DURABLE_ENABLED", "true")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("AP_AGENT_POST_PROCESS_BACKOFF_SECONDS", "0,0,0")
    item = _create_validated_item(db, gmail_id="thread-post-process-fail", correlation_id="corr-post-process-dead")
    invoice = _invoice_from_item(item)

    orch = _new_orchestrator(db)
    job = orch._enqueue_post_process_job(
        invoice,
        agent_decision=None,
        workflow_result={"status": "approved", "erp_status": "success"},
    )
    assert job is not None

    monkeypatch.setattr(orch, "_run_post_process_steps", AsyncMock(side_effect=RuntimeError("post_process_fail")))

    first = asyncio.run(orch.process_due_post_process_jobs(limit=10))
    assert first["claimed"] == 1
    assert first["rescheduled"] == 1

    jobs_after_first = [j for j in db.list_agent_retry_jobs("default", ap_item_id=item["id"]) if j.get("job_type") == "post_process"]
    assert jobs_after_first[0]["status"] == "pending"
    assert int(jobs_after_first[0]["retry_count"]) == 1

    db.reschedule_agent_retry_job(
        jobs_after_first[0]["id"],
        next_retry_at=datetime.now(timezone.utc).isoformat(),
        last_error=jobs_after_first[0].get("last_error"),
        result=jobs_after_first[0].get("result") or {},
        status="pending",
    )

    orch_restart = _new_orchestrator(db)
    monkeypatch.setattr(orch_restart, "_run_post_process_steps", AsyncMock(side_effect=RuntimeError("post_process_fail")))
    second = asyncio.run(orch_restart.process_due_post_process_jobs(limit=10))
    assert second["claimed"] == 1
    assert second["dead_letter"] == 1

    final_jobs = [j for j in db.list_agent_retry_jobs("default", ap_item_id=item["id"]) if j.get("job_type") == "post_process"]
    assert final_jobs[0]["status"] == "dead_letter"
    assert int(final_jobs[0]["retry_count"]) == 2

    audits = db.list_ap_audit_events(item["id"])
    event_types = [a.get("event_type") for a in audits]
    assert "agent_post_process_rescheduled" in event_types
    assert "agent_post_process_dead_letter" in event_types
