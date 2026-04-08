from __future__ import annotations

from unittest.mock import AsyncMock, patch

from clearledgr.core.database import ClearledgrDB
from clearledgr.core.finance_contracts import ActionExecution, SkillRequest
from clearledgr.services.agent_memory import AgentMemoryService
from clearledgr.services.finance_agent_loop import FinanceAgentLoopService


class _GovernanceRuntime:
    def __init__(self, db: ClearledgrDB, *, autonomous_allowed: bool = True) -> None:
        self.organization_id = "test-org"
        self.db = db
        self.actor_id = "tester"
        self.actor_email = "tester@example.com"
        self._autonomous_allowed = autonomous_allowed

    def _resolve_ap_item(self, entity_id: str):
        return self.db.get_ap_item(entity_id)

    @staticmethod
    def parse_json_dict(raw):
        return dict(raw or {}) if isinstance(raw, dict) else {}

    def preview_skill_request(self, request):
        return {"status": "ready", "intent": request.task_type}

    @staticmethod
    def _normalize_vendor_name(value):
        return str(value or "").strip()

    def skill_readiness(self, _skill_id: str, *, window_hours: int = 168):
        return {
            "status": "ready",
            "gates": [
                {"gate": "legal_transition_correctness", "status": "pass"},
                {"gate": "idempotency_integrity", "status": "pass"},
                {"gate": "audit_coverage", "status": "pass"},
                {"gate": "operator_acceptance", "status": "pass"},
                {"gate": "enabled_connector_readiness", "status": "pass"},
            ],
            "metrics": {},
            "window_hours": window_hours,
        }

    def ap_autonomy_policy(self, *, vendor_name=None, action="route_low_risk_for_approval", autonomous_requested=False, ap_item=None):
        return {
            "mode": "auto" if self._autonomous_allowed else "manual",
            "action": action,
            "autonomous_requested": autonomous_requested,
            "autonomous_allowed": bool(self._autonomous_allowed),
            "reason_codes": [] if self._autonomous_allowed else ["manual_mode"],
            "vendor_shadow_scored_item_count": 5,
            "vendor_shadow_action_match_rate": 0.95,
            "vendor_shadow_critical_field_match_rate": 0.97,
            "vendor_post_verification_attempt_count": 3,
            "vendor_post_verification_rate": 1.0,
            "vendor_post_verification_mismatch_count": 0,
            "failing_gates": [],
            "blocked_actions": {},
            "earned_actions": ["route_low_risk_for_approval", "auto_approve", "post_to_erp"],
        }


def test_finance_agent_loop_blocks_doctrine_forbidden_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "governance-block.db"))
    db.initialize()
    db.create_ap_item(
        {
            "id": "ap-block-1",
            "organization_id": "test-org",
            "thread_id": "thread-block-1",
            "state": "validated",
            "vendor_name": "Blocked Vendor",
            "invoice_number": "INV-BLOCK-1",
            "amount": 10.0,
            "currency": "USD",
            "metadata": {"document_type": "invoice", "processing_status": "validated"},
        }
    )
    memory = AgentMemoryService("test-org", db=db)
    memory.ensure_profile(
        skill_id="ap_v1",
        profile_overrides={"forbidden_actions": ["post_to_erp"]},
    )

    runtime = _GovernanceRuntime(db, autonomous_allowed=True)
    loop = FinanceAgentLoopService(runtime)
    request = SkillRequest.from_intent(
        org_id="test-org",
        task_type="post_to_erp",
        skill_id="ap_v1",
        entity_id="ap-block-1",
        correlation_id="corr-block-1",
        payload={"ap_item_id": "ap-block-1"},
    )
    action = ActionExecution(
        entity_id="ap-block-1",
        action="post_to_erp",
        preview=False,
        idempotency_key="governance-block",
    )

    executor = AsyncMock(return_value={"status": "posted_to_erp"})
    response = __import__("asyncio").run(loop.run_skill_request(request, action, executor))

    assert response["status"] == "blocked"
    assert response["reason"] == "doctrine_enforced_block"
    assert "forbidden_action:post_to_erp" in response["deliberation"]["doctrine"]["reason_codes"]
    executor.assert_not_awaited()


def test_finance_agent_loop_attempts_self_recovery_for_failed_post(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "governance-recovery.db"))
    db.initialize()
    db.create_ap_item(
        {
            "id": "ap-recover-1",
            "organization_id": "test-org",
            "thread_id": "thread-recover-1",
            "state": "failed_post",
            "vendor_name": "Recover Vendor",
            "invoice_number": "INV-RECOVER-1",
            "amount": 20.0,
            "currency": "USD",
            "last_error": "connector timeout",
            "metadata": {"document_type": "invoice", "processing_status": "failed_post"},
        }
    )

    runtime = _GovernanceRuntime(db, autonomous_allowed=True)
    loop = FinanceAgentLoopService(runtime)
    request = SkillRequest.from_intent(
        org_id="test-org",
        task_type="retry_recoverable_failures",
        skill_id="ap_v1",
        entity_id="ap-recover-1",
        correlation_id="corr-recover-1",
        payload={"ap_item_id": "ap-recover-1"},
    )
    action = ActionExecution(
        entity_id="ap-recover-1",
        action="retry_recoverable_failures",
        preview=False,
        idempotency_key="governance-recover",
    )

    class _Workflow:
        async def resume_workflow(self, ap_item_id: str):
            assert ap_item_id == "ap-recover-1"
            return {"status": "posted_to_erp", "erp_reference": "ERP-REC-1"}

    async def _executor():
        return {"status": "failed", "reason": "connector_timeout", "ap_item_id": "ap-recover-1"}

    with patch("clearledgr.services.invoice_workflow.get_invoice_workflow", return_value=_Workflow()):
        response = __import__("asyncio").run(loop.run_skill_request(request, action, _executor))

    assert response["status"] == "posted_to_erp"
    assert response["self_recovery"]["attempted"] is True
    assert response["self_recovery"]["recovered"] is True
    assert response["recovery_succeeded"] is True


def test_finance_agent_loop_allows_manual_risky_action_to_reach_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "governance-manual.db"))
    db.initialize()
    db.create_ap_item(
        {
            "id": "ap-manual-1",
            "organization_id": "test-org",
            "thread_id": "thread-manual-1",
            "state": "ready_to_post",
            "vendor_name": "Manual Vendor",
            "invoice_number": "INV-MANUAL-1",
            "amount": 30.0,
            "currency": "USD",
            "metadata": {
                "document_type": "invoice",
                "processing_status": "ready_to_post",
            },
        }
    )

    runtime = _GovernanceRuntime(db, autonomous_allowed=False)
    loop = FinanceAgentLoopService(runtime)
    request = SkillRequest.from_intent(
        org_id="test-org",
        task_type="post_to_erp",
        skill_id="ap_v1",
        entity_id="ap-manual-1",
        correlation_id="corr-manual-1",
        payload={"ap_item_id": "ap-manual-1"},
    )
    action = ActionExecution(
        entity_id="ap-manual-1",
        action="post_to_erp",
        preview=False,
        idempotency_key="governance-manual",
    )

    executor = AsyncMock(return_value={"status": "blocked", "reason": "field_review_required"})
    response = __import__("asyncio").run(loop.run_skill_request(request, action, executor))

    assert response["status"] == "blocked"
    assert response["reason"] == "field_review_required"
    assert response["deliberation"]["autonomous_requested"] is False
    assert response["deliberation"]["doctrine"]["blocked"] is False
    executor.assert_awaited_once()
