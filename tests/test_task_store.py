from __future__ import annotations

from clearledgr.core.database import ClearledgrDB


def test_create_task_run_resets_failed_idempotent_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")

    db = ClearledgrDB(str(tmp_path / "task-store.db"))
    first = db.create_task_run(
        id="task-1",
        org_id="default",
        task_type="ap_invoice_processing",
        input_payload='{"invoice":{"gmail_id":"msg-1"}}',
        idempotency_key="invoice:msg-1",
        correlation_id="corr-1",
    )
    db.fail_task_run(first["id"], "anthropic_400")

    retried = db.create_task_run(
        id="task-2",
        org_id="default",
        task_type="ap_invoice_processing",
        input_payload='{"invoice":{"gmail_id":"msg-1","thread_id":"thread-1"}}',
        idempotency_key="invoice:msg-1",
        correlation_id="corr-2",
    )

    assert retried["id"] == first["id"]
    assert retried["status"] == "pending"
    assert retried["current_step"] == 0
    assert retried["step_results"] == "{}"
    assert retried["last_error"] is None
    assert retried["retry_count"] == 1
    assert retried["correlation_id"] == "corr-2"
    assert retried["input_payload"] == '{"invoice":{"gmail_id":"msg-1","thread_id":"thread-1"}}'
