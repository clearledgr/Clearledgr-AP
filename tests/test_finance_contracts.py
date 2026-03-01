from __future__ import annotations

from clearledgr.core.finance_contracts import (
    ActionExecution,
    AuditEvent,
    SkillCapabilityManifest,
    SkillRequest,
    SkillResponse,
)


def test_skill_request_from_intent_normalizes_defaults():
    request = SkillRequest.from_intent(
        org_id="default",
        skill_id="ap_v1",
        task_type="Route_Low_Risk_For_Approval",
        payload={"email_id": "gmail-thread-1"},
    )
    assert request.org_id == "default"
    assert request.skill_id == "ap_v1"
    assert request.task_type == "route_low_risk_for_approval"
    assert request.payload["email_id"] == "gmail-thread-1"


def test_skill_response_from_legacy_preserves_details_and_contract_fields():
    legacy = {
        "status": "blocked",
        "next_step": "resolve_blockers",
        "policy_precheck": {"reason_codes": ["approval_pending"]},
        "email_id": "gmail-thread-1",
        "ap_item_id": "ap-item-1",
    }
    response = SkillResponse.from_legacy(legacy)
    data = response.to_dict()
    assert data["status"] == "blocked"
    assert data["recommended_next_action"] == "resolve_blockers"
    assert data["blockers"] == ["approval_pending"]
    assert "gmail-thread-1" in data["evidence_refs"]
    assert "ap-item-1" in data["evidence_refs"]


def test_action_execution_to_dict():
    action = ActionExecution(
        entity_id="ap-item-1",
        action="route_low_risk_for_approval",
        preview=False,
        idempotency_key="idem-1",
        reason="manual override",
    )
    payload = action.to_dict()
    assert payload["action"] == "route_low_risk_for_approval"
    assert payload["idempotency_key"] == "idem-1"
    assert payload["reason"] == "manual override"


def test_audit_event_to_dict_has_required_fields():
    event = AuditEvent(
        org_id="default",
        skill_id="ap_v1",
        entity_id="ap-item-1",
        action="route_low_risk_for_approval",
        actor="agent",
        outcome="pending_approval",
        correlation_id="corr-1",
        evidence_refs=["gmail-thread-1"],
    )
    data = event.to_dict()
    assert data["org_id"] == "default"
    assert data["skill_id"] == "ap_v1"
    assert data["entity_id"] == "ap-item-1"
    assert data["correlation_id"] == "corr-1"
    assert data["evidence_refs"] == ["gmail-thread-1"]
    assert data["timestamp"]
    assert data["event_id"]


def test_skill_capability_manifest_validates_required_sections():
    manifest = SkillCapabilityManifest(
        skill_id="ap_v1",
        version="1.0",
        state_machine={"primary_path": ["received", "validated"]},
        action_catalog=[{"intent": "route_low_risk_for_approval"}],
        policy_pack={"deterministic_prechecks": ["state_guard"]},
        evidence_schema={"material_refs": ["ap_item_id"]},
        adapter_bindings={"erp": ["netsuite", "sap", "quickbooks", "xero"]},
        kpi_contract={"promotion_gates": {"audit_coverage_min": 0.99}},
    )
    payload = manifest.to_dict()
    assert payload["is_valid"] is True
    assert payload["missing_requirements"] == []


def test_skill_capability_manifest_reports_missing_sections():
    manifest = SkillCapabilityManifest(skill_id="", version="")
    payload = manifest.to_dict()
    assert payload["is_valid"] is False
    assert "skill_id" in payload["missing_requirements"]
    assert "action_catalog" in payload["missing_requirements"]
