from __future__ import annotations

import asyncio

from clearledgr.services import agent_retry_jobs as retry_jobs_module
from clearledgr.services.finance_agent_runtime import FinanceAgentRuntime


class _FakeRetryDB:
    def __init__(self) -> None:
        self.jobs = [
            {
                "id": "job-1",
                "organization_id": "default",
                "job_type": "erp_post_retry",
                "ap_item_id": "ap-1",
                "retry_count": 0,
                "max_retries": 3,
            }
        ]
        self.completed = []
        self.rescheduled = []

    def list_due_agent_retry_jobs(self, organization_id=None, limit=25):
        _ = organization_id, limit
        return list(self.jobs)

    def claim_agent_retry_job(self, job_id, *, worker_id):
        _ = worker_id
        for job in self.jobs:
            if job["id"] == job_id:
                return dict(job)
        return None

    def complete_agent_retry_job(self, job_id, *, status="completed", result=None, last_error=None):
        self.completed.append((job_id, status, result, last_error))
        return True

    def reschedule_agent_retry_job(self, job_id, *, next_retry_at, last_error=None, result=None, status="pending"):
        self.rescheduled.append((job_id, next_retry_at, last_error, result, status))
        return True


def test_drain_agent_retry_jobs_recovers_due_jobs(monkeypatch):
    fake_db = _FakeRetryDB()

    class _FakeWorkflow:
        async def resume_workflow(self, ap_item_id):
            assert ap_item_id == "ap-1"
            return {"status": "recovered"}

    monkeypatch.setattr(retry_jobs_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(retry_jobs_module, "get_invoice_workflow", lambda _org_id: _FakeWorkflow())

    summary = asyncio.run(
        retry_jobs_module.drain_agent_retry_jobs(
            organization_id="default",
            worker_id_prefix="runtime_resume",
        )
    )

    assert summary["claimed"] == 1
    assert summary["completed"] == 1
    assert fake_db.completed[0][0] == "job-1"


def test_runtime_resume_pending_agent_tasks_uses_retry_drain(monkeypatch):
    runtime = FinanceAgentRuntime(
        organization_id="default",
        actor_id="system",
        actor_email="system@example.com",
        db=object(),
    )

    async def _fake_drain(**kwargs):
        assert kwargs["organization_id"] == "default"
        return {"claimed": 1, "completed": 1, "rescheduled": 0, "dead_letter": 0}

    monkeypatch.setattr(
        "clearledgr.services.agent_retry_jobs.drain_agent_retry_jobs",
        _fake_drain,
    )

    summary = asyncio.run(runtime.resume_pending_agent_tasks())

    assert summary["completed"] == 1
