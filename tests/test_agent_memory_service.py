from __future__ import annotations

import json

from clearledgr.core.database import ClearledgrDB
from clearledgr.services.agent_memory import AgentMemoryService


def test_agent_memory_service_persists_profile_events_and_belief(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "agent-memory.db"))
    db.initialize()

    service = AgentMemoryService("test-org", db=db)

    profile = service.ensure_profile(
        skill_id="ap_v1",
        profile_overrides={"autonomy_level": "bounded_auto"},
    )
    event = service.observe_event(
        skill_id="ap_v1",
        ap_item_id="ap-1",
        thread_id="thread-1",
        event_type="ap_invoice_processing_completed",
        payload={"response": {"status": "pending_approval"}},
        actor_id="agent@example.com",
        correlation_id="corr-agent-memory-1",
        summary="queued_for_approval",
    )
    snapshot = service.capture_runtime_state(
        skill_id="ap_v1",
        ap_item={
            "id": "ap-1",
            "thread_id": "thread-1",
            "message_id": "message-1",
            "state": "validated",
            "vendor_name": "Acme Co",
            "invoice_number": "INV-1",
            "amount": 120.0,
            "currency": "USD",
            "metadata": {"document_type": "invoice"},
        },
        event_type="ap_invoice_processing_completed",
        reason="ap_invoice_processing_pending_approval",
        response={"status": "pending_approval", "reason": "requires_approval"},
        actor_id="agent@example.com",
        correlation_id="corr-agent-memory-1",
    )

    belief = service.get_belief_state(ap_item_id="ap-1")
    episode = service.get_episode_summary(ap_item_id="ap-1")
    events = service.list_memory_events(ap_item_id="ap-1")

    assert profile["autonomy_level"] == "bounded_auto"
    assert event is not None
    assert snapshot is not None
    assert len(events) == 1
    assert events[0]["event_type"] == "ap_invoice_processing_completed"
    assert belief["belief"]["vendor_name"] == "Acme Co"
    assert belief["next_action"]["type"] == "await_approval"
    assert belief["summary"]["profile"]["name"] == "Clearledgr AP Agent"
    assert episode["status"] == "pending_approval"
    assert episode["outcome"]["next_action"]["type"] == "await_approval"


def test_agent_memory_service_recall_patterns_and_record_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "agent-memory-recall.db"))
    db.initialize()

    db.create_ap_item(
        {
            "id": "ap-2",
            "organization_id": "test-org",
            "thread_id": "thread-2",
            "state": "validated",
            "vendor_name": "Northwind",
            "invoice_number": "INV-2",
            "amount": 88.0,
            "currency": "USD",
            "metadata": {
                "document_type": "invoice",
                "processing_status": "pending_approval",
                "correlation_id": "corr-agent-memory-2",
            },
        }
    )

    service = AgentMemoryService("test-org", db=db)

    belief = service.build_belief_state(ap_item_id="ap-2", skill_id="ap_v1")
    pattern = service.record_pattern(
        skill_id="ap_v1",
        pattern_type="vendor_document_next_action",
        pattern_key="northwind|invoice|await_approval",
        pattern={
            "vendor_name": "Northwind",
            "document_type": "invoice",
            "current_state": "validated",
            "status": "pending_approval",
            "next_action": {"type": "await_approval", "label": "Wait for approval decision"},
            "reason": "Pending approver response",
        },
        confidence=0.91,
    )
    recall = service.recall_similar_cases(
        {
            "vendor_name": "Northwind",
            "document_type": "invoice",
            "current_state": "validated",
            "next_action": "await_approval",
        },
        skill_id="ap_v1",
        limit=3,
    )
    outcome = service.record_outcome(
        skill_id="ap_v1",
        ap_item=db.get_ap_item("ap-2"),
        ap_item_id="ap-2",
        event_type="approval_request_routed",
        reason="awaiting_assigned_approver",
        response={"status": "pending_approval", "next_step": "await_approval"},
        actor_id="agent@example.com",
        source="finance_agent_loop",
        correlation_id="corr-agent-memory-2",
    )
    episode = service.summarize_episode(ap_item_id="ap-2", skill_id="ap_v1")
    patterns = service.list_patterns(
        skill_id="ap_v1",
        pattern_type="vendor_document_next_action",
        pattern_key_prefix="northwind|invoice|",
        limit=5,
    )

    assert belief["belief"]["vendor_name"] == "Northwind"
    assert pattern["pattern_key"] == "northwind|invoice|await_approval"
    assert patterns[0]["usage_count"] >= 1
    assert recall
    assert recall[0]["score"] >= 1
    assert outcome["event"]["event_type"] == "approval_request_routed"
    assert episode["status"] == "pending_approval"


def test_agent_memory_surface_aggregates_semantic_and_episodic_context(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "agent-memory-surface.db"))
    db.initialize()

    db.create_ap_item(
        {
            "id": "ap-3",
            "organization_id": "test-org",
            "thread_id": "thread-3",
            "state": "failed_post",
            "vendor_name": "Surface Vendor",
            "invoice_number": "INV-3",
            "amount": 55.0,
            "currency": "USD",
            "metadata": {
                "document_type": "invoice",
                "processing_status": "failed_post",
                "correlation_id": "corr-agent-memory-3",
            },
        }
    )
    db.upsert_vendor_profile("test-org", "Surface Vendor", payment_terms="Net 15", invoice_count=3)
    db.record_vendor_decision_feedback(
        "test-org",
        "Surface Vendor",
        ap_item_id="ap-3",
        human_decision="approve",
        agent_recommendation="approve",
        reason="trusted_vendor",
    )
    db.create_workflow_run(
        {
            "organization_id": "test-org",
            "workflow_name": "erp_post_retry",
            "workflow_type": "recovery",
            "ap_item_id": "ap-3",
            "status": "running",
            "metadata": {"source": "test"},
        }
    )
    db.create_agent_retry_job(
        {
            "organization_id": "test-org",
            "ap_item_id": "ap-3",
            "job_type": "erp_post_retry",
            "status": "pending",
        }
    )
    db.create_task_run(
        id="task-run-ap-3",
        org_id="test-org",
        task_type="ap_invoice_processing",
        input_payload=json.dumps({"ap_item_id": "ap-3"}),
        correlation_id="corr-task-ap-3",
    )

    service = AgentMemoryService("test-org", db=db)
    service.capture_runtime_state(
        skill_id="ap_v1",
        ap_item=db.get_ap_item("ap-3"),
        ap_item_id="ap-3",
        event_type="erp_api_failed",
        reason="api_posting_failed",
        response={"status": "failed_post"},
        actor_id="tester",
        correlation_id="corr-agent-memory-3",
    )

    surface = service.build_surface(ap_item_id="ap-3", skill_id="ap_v1")

    assert surface["identity_memory"]["name"] == "Clearledgr AP Agent"
    assert surface["semantic_memory"]["vendor_profile"]["payment_terms"] == "Net 15"
    assert surface["semantic_memory"]["vendor_feedback_summary"]["total_feedback"] == 1
    assert surface["episodic_memory"]["workflow_runs"]
    assert surface["episodic_memory"]["retry_jobs"]
    assert surface["episodic_memory"]["task_runs"]


def test_agent_memory_compacts_old_events_and_persists_eval_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "agent-memory-compact.db"))
    db.initialize()

    service = AgentMemoryService("test-org", db=db)
    db.create_ap_item(
        {
            "id": "ap-compact-1",
            "organization_id": "test-org",
            "thread_id": "thread-compact-1",
            "state": "validated",
            "vendor_name": "Compact Vendor",
            "invoice_number": "INV-COMPACT-1",
            "amount": 22.0,
            "currency": "USD",
            "metadata": {"document_type": "invoice", "processing_status": "validated"},
        }
    )

    for idx in range(14):
        service.observe_event(
            skill_id="ap_v1",
            ap_item_id="ap-compact-1",
            thread_id="thread-compact-1",
            event_type=f"event_{idx}",
            payload={"step": idx},
            actor_id="tester",
            summary=f"event-{idx}",
        )

    compacted = service.compact_memory(ap_item_id="ap-compact-1", skill_id="ap_v1", keep_recent=5)
    events = service.list_memory_events(ap_item_id="ap-compact-1")
    service.record_eval_snapshot(
        skill_id="ap_v1",
        scope="ap_item",
        scope_id="ap-compact-1",
        snapshot_type="quality_snapshot",
        payload={"proof_status": "observe", "requested_action": "route_low_risk_for_approval"},
    )
    surface = service.build_surface(ap_item_id="ap-compact-1", skill_id="ap_v1")

    assert compacted["compacted"] == 9
    assert len(events) == 5
    assert surface["episode"]["outcome"]["compacted_history"]
    assert surface["latest_eval"]["payload"]["proof_status"] == "observe"


def test_agent_memory_ensure_profile_preserves_existing_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "agent-memory-profile.db"))
    db.initialize()

    service = AgentMemoryService("test-org", db=db)
    service.ensure_profile(
        skill_id="ap_v1",
        profile_overrides={
            "forbidden_actions": ["post_to_erp"],
            "autonomy_level": "bounded_auto",
        },
    )
    preserved = service.ensure_profile(skill_id="ap_v1")

    assert preserved["forbidden_actions"] == ["post_to_erp"]
    assert preserved["autonomy_level"] == "bounded_auto"
