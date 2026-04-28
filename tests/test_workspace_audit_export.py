"""Tests for ``POST /api/workspace/audit/export`` + status/download.

Module 7 v1 Pass 2 — async CSV export. Exercises the full job
lifecycle inline (no Celery worker needed) by calling the task
function directly with the export_id the POST returned. Covers:

  * Admin gate.
  * Tenant scope on POST + GET.
  * 404 for cross-tenant exports.
  * Status lifecycle: queued → running → done.
  * CSV content sanity: header columns + row count matches search.
  * Download response: status, headers, body.
  * 409 when downloading an export that hasn't finished yet.
  * Failed-job path: errors surface via status payload's
    error_message, the SPA can render them.
"""
from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api import workspace_shell as ws  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402
from clearledgr.core.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("default", organization_name="default")
    inst.ensure_organization("other-tenant", organization_name="other-tenant")
    return inst


def _admin_user(org_id: str = "default"):
    return SimpleNamespace(
        email="admin@example.com",
        user_id="admin-user",
        organization_id=org_id,
        role="owner",
    )


def _operator_user():
    return SimpleNamespace(
        email="ops@example.com",
        user_id="ops-user",
        organization_id="default",
        role="ap_clerk",
    )


@pytest.fixture()
def client_factory(db):
    def _build(user_factory):
        app = FastAPI()
        app.include_router(ws.router)
        app.dependency_overrides[get_current_user] = user_factory
        return TestClient(app)
    return _build


def _seed_event(
    db,
    *,
    box_id: str,
    event_type: str = "state_transition",
    organization_id: str = "default",
    actor_id: str = "admin@example.com",
    box_type: str = "ap_item",
    ts: str | None = None,
):
    payload = {
        "box_id": box_id,
        "box_type": box_type,
        "event_type": event_type,
        "actor_type": "user",
        "actor_id": actor_id,
        "organization_id": organization_id,
        "source": "test_seed",
        "payload_json": {"reason": "test"},
        "idempotency_key": f"audit_export_test:{organization_id}:{box_id}:{event_type}:{ts or time.time_ns()}",
    }
    if ts:
        payload["ts"] = ts
    return db.append_audit_event(payload)


def _run_task_inline(export_id: str):
    """Drive the Celery task in-process so tests don't need a worker.

    The task is decorated with ``@app.task(bind=True, ...)``. Celery
    binds the task instance as ``self`` automatically when ``.run``
    or ``.apply()`` is invoked, so the test calls ``.run(export_id)``
    directly — Celery handles the ``self`` plumbing for us.
    """
    from clearledgr.services.celery_tasks import generate_audit_export
    return generate_audit_export.run(export_id)


# ---------------------------------------------------------------------------
# Role + tenant gate
# ---------------------------------------------------------------------------


def test_export_requires_admin(client_factory):
    client = client_factory(_operator_user)
    resp = client.post(
        "/api/workspace/audit/export",
        json={"organization_id": "default"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_role_required"


def test_export_blocks_cross_tenant_post(client_factory):
    client = client_factory(_admin_user)
    resp = client.post(
        "/api/workspace/audit/export",
        json={"organization_id": "other-tenant"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "org_access_denied"


def test_export_status_404_for_cross_tenant_job(db, client_factory):
    """Status poll for an export that belongs to another tenant
    returns 404 with the same token as truly-missing — never leaks
    that the job exists in another tenant."""
    other_export = db.create_audit_export(
        organization_id="other-tenant",
        requested_by="someone@other.com",
        filters_json="{}",
    )
    client = client_factory(_admin_user)
    resp = client.get(f"/api/workspace/audit/exports/{other_export['id']}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "audit_export_not_found"


def test_export_status_404_for_missing_job(client_factory):
    client = client_factory(_admin_user)
    resp = client.get("/api/workspace/audit/exports/AEX-does-not-exist-9999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "audit_export_not_found"


# ---------------------------------------------------------------------------
# Job lifecycle: queued → running → done with CSV content
# ---------------------------------------------------------------------------


def test_export_full_lifecycle_writes_csv(db, client_factory):
    # Seed three events the export should pick up.
    _seed_event(db, box_id="ap-export-1", event_type="state_transition")
    _seed_event(db, box_id="ap-export-2", event_type="invoice_approved")
    _seed_event(db, box_id="ap-export-3", event_type="erp_post_completed")

    client = client_factory(_admin_user)
    # Mock out the Celery dispatch — the test runs the task inline
    # afterwards so we control timing.
    with patch("clearledgr.services.celery_tasks.generate_audit_export") as mock_task:
        # The endpoint references ``.delay``; make it a no-op.
        mock_task.delay.return_value = None
        post_resp = client.post(
            "/api/workspace/audit/export",
            json={"organization_id": "default"},
        )
    assert post_resp.status_code == 200
    body = post_resp.json()
    job_id = body["job_id"]
    assert body["status"] == "queued"
    # delay() was called exactly once with the new job_id
    mock_task.delay.assert_called_once_with(job_id)

    # Status poll before running the task: still queued.
    poll_resp = client.get(f"/api/workspace/audit/exports/{job_id}")
    assert poll_resp.status_code == 200
    assert poll_resp.json()["status"] == "queued"

    # Run the task inline.
    result = _run_task_inline(job_id)
    assert result["status"] == "done"
    assert result["rows"] >= 3

    # Status poll after running: done with row count + size.
    poll_resp = client.get(f"/api/workspace/audit/exports/{job_id}")
    assert poll_resp.status_code == 200
    body = poll_resp.json()
    assert body["status"] == "done"
    assert body["total_rows"] >= 3
    assert body["content_size_bytes"] > 0
    assert body["content_filename"].startswith("audit-default-")
    assert body["content_filename"].endswith(".csv")
    assert body["completed_at"] is not None
    # Status payload must NOT include the content blob.
    assert "content" not in body

    # Download the actual CSV.
    dl_resp = client.get(f"/api/workspace/audit/exports/{job_id}?download=true")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"].startswith("text/csv")
    assert "attachment;" in dl_resp.headers["content-disposition"]
    csv_text = dl_resp.text
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    # Header + at least the 3 seeded rows.
    header = rows[0]
    assert "id" in header
    assert "event_type" in header
    assert "organization_id" in header
    assert len(rows) >= 4  # header + 3 events
    # The seeded event_types appear somewhere in the body.
    body_str = "\n".join(",".join(r) for r in rows[1:])
    assert "state_transition" in body_str
    assert "invoice_approved" in body_str
    assert "erp_post_completed" in body_str


def test_export_filters_apply_to_csv_content(db, client_factory):
    """Filters submitted at POST time must narrow the CSV content the
    same way GET /search does — same filter contract, two surfaces."""
    _seed_event(db, box_id="ap-filter-1", event_type="state_transition")
    _seed_event(db, box_id="ap-filter-1", event_type="invoice_approved")
    _seed_event(db, box_id="ap-filter-2", event_type="state_transition")

    client = client_factory(_admin_user)
    with patch("clearledgr.services.celery_tasks.generate_audit_export") as mock_task:
        mock_task.delay.return_value = None
        post_resp = client.post(
            "/api/workspace/audit/export",
            json={
                "organization_id": "default",
                "box_id": "ap-filter-1",
                "event_types": ["invoice_approved"],
            },
        )
    job_id = post_resp.json()["job_id"]

    _run_task_inline(job_id)
    dl_resp = client.get(f"/api/workspace/audit/exports/{job_id}?download=true")
    rows = list(csv.reader(io.StringIO(dl_resp.text)))
    body_rows = rows[1:]
    # Only the invoice_approved row from ap-filter-1 should land in the CSV.
    assert all("ap-filter-1" in ",".join(r) for r in body_rows)
    assert all("invoice_approved" in ",".join(r) for r in body_rows)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_export_download_409_when_not_done(db, client_factory):
    """Downloading an export that's still queued returns 409 with
    a clear reason — UI shouldn't poll-then-download race."""
    client = client_factory(_admin_user)
    with patch("clearledgr.services.celery_tasks.generate_audit_export") as mock_task:
        mock_task.delay.return_value = None
        post_resp = client.post(
            "/api/workspace/audit/export",
            json={"organization_id": "default"},
        )
    job_id = post_resp.json()["job_id"]

    dl_resp = client.get(f"/api/workspace/audit/exports/{job_id}?download=true")
    assert dl_resp.status_code == 409
    detail = dl_resp.json()["detail"]
    assert detail["reason"] == "export_not_ready"
    assert detail["status"] == "queued"


def test_export_dispatch_failure_marks_failed(db, client_factory):
    """If Celery dispatch raises (broker outage etc), the row gets
    flipped to 'failed' rather than left in 'queued' forever."""
    client = client_factory(_admin_user)
    with patch("clearledgr.services.celery_tasks.generate_audit_export") as mock_task:
        mock_task.delay.side_effect = RuntimeError("broker unreachable")
        post_resp = client.post(
            "/api/workspace/audit/export",
            json={"organization_id": "default"},
        )
    assert post_resp.status_code == 200
    body = post_resp.json()
    assert body["status"] == "failed"

    poll_resp = client.get(f"/api/workspace/audit/exports/{body['job_id']}")
    assert poll_resp.status_code == 200
    poll_body = poll_resp.json()
    assert poll_body["status"] == "failed"
    assert "broker unreachable" in (poll_body["error_message"] or "")


def test_reap_expired_audit_exports_drops_old_rows(db):
    """Reaper sweeps rows past expires_at; in-flight rows untouched."""
    # Force-expired
    expired = db.create_audit_export(
        organization_id="default",
        requested_by="admin@example.com",
        filters_json="{}",
        retention_hours=-1,  # already expired
    )
    # Fresh
    fresh = db.create_audit_export(
        organization_id="default",
        requested_by="admin@example.com",
        filters_json="{}",
        retention_hours=24,
    )

    deleted = db.reap_expired_audit_exports()
    assert deleted >= 1

    assert db.get_audit_export(expired["id"]) is None
    assert db.get_audit_export(fresh["id"]) is not None
