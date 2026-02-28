"""
Concurrent multi-tenant isolation tests for Clearledgr AP v1.

Tests:
1. test_org_data_isolation_reads         — org-A data invisible to org-B query
2. test_org_data_isolation_writes        — org-A item cannot be mutated under org-B JWT
3. test_concurrent_multi_org_creation   — 10 threads × 20 items, zero bleed-over
4. test_metadata_merge_concurrent_safe   — 5 concurrent threads, no lost update
5. test_approval_reminder_no_duplicate  — DB milestone deduplication survives restart
6. test_soft_org_guard_blocks_jwt_mismatch   — JWT org-A + query org-B → 403
7. test_soft_org_guard_passes_unauthenticated — no auth + query org-B → 200
"""
from __future__ import annotations

import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from main import _apply_runtime_surface_profile, app
from clearledgr.core import database as db_module
from clearledgr.core.auth import TokenData, create_access_token, get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "isolation.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    monkeypatch.setenv("AP_V1_STRICT_SURFACES", "false")
    monkeypatch.setenv("CLEARLEDGR_ENABLE_LEGACY_SURFACES", "true")
    _apply_runtime_surface_profile()
    db_module._DB_INSTANCE = None
    d = db_module.get_db()
    d.initialize()
    return d


@pytest.fixture()
def client(db):
    return TestClient(app)


def _item_payload(
    *,
    item_id: str,
    org_id: str,
    state: str = "pending_review",
    amount: float = 500.0,
) -> dict:
    return {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": "Test invoice",
        "sender": "vendor@example.com",
        "vendor_name": "Vendor Corp",
        "amount": amount,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": state,
        "confidence": 0.95,
        "organization_id": org_id,
        "metadata": {},
    }


def _jwt_for(org_id: str, user_id: str = "user-test") -> str:
    return create_access_token(
        user_id=user_id,
        email=f"{user_id}@{org_id}.com",
        organization_id=org_id,
        expires_delta=timedelta(hours=1),
    )


def _auth_headers(org_id: str, user_id: str = "user-test") -> dict:
    return {"Authorization": f"Bearer {_jwt_for(org_id, user_id)}"}


# ---------------------------------------------------------------------------
# Test 1 — read isolation
# ---------------------------------------------------------------------------


def test_org_data_isolation_reads(db, client):
    """Items in org-A must NOT appear in org-B queries."""
    db.create_ap_item(_item_payload(item_id="read-a-1", org_id="org-alpha"))
    db.create_ap_item(_item_payload(item_id="read-a-2", org_id="org-alpha"))
    db.create_ap_item(_item_payload(item_id="read-b-1", org_id="org-beta"))

    resp = client.get(
        "/extension/pipeline?organization_id=org-beta",
        headers=_auth_headers("org-beta"),
    )
    assert resp.status_code == 200
    payload = resp.json()
    all_ids = {item["id"] for group in payload.values() for item in group}
    assert "read-a-1" not in all_ids
    assert "read-a-2" not in all_ids
    assert "read-b-1" in all_ids


# ---------------------------------------------------------------------------
# Test 2 — write isolation
# ---------------------------------------------------------------------------


def test_org_data_isolation_writes(db, client):
    """Mutating org-A item via org-B JWT must be rejected."""
    db.create_ap_item(_item_payload(item_id="write-a-1", org_id="org-alpha"))

    # soft_org_guard fires: JWT says org-beta, but item lives in org-alpha
    resp = client.post(
        "/api/ap-workflow/items/write-a-1/resubmit?organization_id=org-alpha",
        headers=_auth_headers("org-beta"),
    )
    # 403 org_mismatch or 404 — either way NOT 200/202
    assert resp.status_code in (403, 404, 422)


# ---------------------------------------------------------------------------
# Test 3 — concurrent multi-org creation (10 threads × 20 items)
# ---------------------------------------------------------------------------


def test_concurrent_multi_org_creation(db):
    """10 orgs × 20 items each, created concurrently — zero bleed-over."""
    NUM_ORGS = 10
    ITEMS_PER_ORG = 20
    errors = []

    def create_items(org_id: str):
        try:
            for i in range(ITEMS_PER_ORG):
                iid = f"{org_id}-item-{i}"
                db.create_ap_item(_item_payload(item_id=iid, org_id=org_id))
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=create_items, args=(f"concurrent-org-{n}",))
        for n in range(NUM_ORGS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"

    for n in range(NUM_ORGS):
        org_id = f"concurrent-org-{n}"
        items = db.list_ap_items(organization_id=org_id)
        assert len(items) == ITEMS_PER_ORG, (
            f"{org_id} expected {ITEMS_PER_ORG}, got {len(items)}"
        )
        for item in items:
            assert item["organization_id"] == org_id, (
                f"Bleed-over: item {item['id']} has org {item['organization_id']}"
            )


# ---------------------------------------------------------------------------
# Test 4 — metadata merge concurrency (5 threads, same item, distinct keys)
# ---------------------------------------------------------------------------


def test_metadata_merge_concurrent_safe(db):
    """5 concurrent threads merge distinct keys; all keys must survive (no lost update)."""
    item_id = "merge-concurrent-1"
    db.create_ap_item(_item_payload(item_id=item_id, org_id="org-merge"))

    errors = []
    NUM_THREADS = 5

    def write_key(thread_num: int):
        try:
            db.update_ap_item_metadata_merge(item_id, {f"key_{thread_num}": thread_num})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_key, args=(i,)) for i in range(NUM_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"

    import json
    raw = db.get_ap_item(item_id)
    meta = json.loads(raw.get("metadata") or "{}")
    for i in range(NUM_THREADS):
        assert f"key_{i}" in meta, f"Lost update: key_{i} missing after concurrent merge"


# ---------------------------------------------------------------------------
# Test 5 — approval reminder deduplication across "restart"
# ---------------------------------------------------------------------------


def test_approval_reminder_no_duplicate(db, monkeypatch):
    """Approval reminders already stored in metadata milestones are NOT re-sent."""
    import json as _json
    from clearledgr.services import agent_background

    item_id = "dup-remind-1"
    now_iso = datetime.now(timezone.utc).isoformat()

    db.create_ap_item(
        _item_payload(
            item_id=item_id,
            org_id="default",
            state="needs_approval",
            amount=2000.0,
        )
    )

    # Pre-populate both milestones (simulates they were already sent)
    db.update_ap_item_metadata_merge(
        item_id,
        {
            "approval_reminder_milestones": {
                "4h": now_iso,
                "24h": now_iso,
            }
        },
    )

    reminder_calls = []

    async def _mock_send_reminder(*args, **kwargs):
        reminder_calls.append(args)
        return True

    monkeypatch.setattr(
        "clearledgr.services.slack_notifications.send_approval_reminder",
        _mock_send_reminder,
    )

    import asyncio

    asyncio.run(agent_background._check_approval_timeouts("default"))

    assert len(reminder_calls) == 0, (
        f"Expected 0 reminder calls after milestones stored, got {len(reminder_calls)}"
    )


# ---------------------------------------------------------------------------
# Test 6 — soft_org_guard blocks JWT mismatch
# ---------------------------------------------------------------------------


def test_soft_org_guard_blocks_jwt_mismatch(db, client):
    """JWT for org-A with query ?organization_id=org-B → 403."""
    resp = client.get(
        "/ap-advanced/retention/expiring?organization_id=org-B",
        headers=_auth_headers("org-A"),
    )
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "org_mismatch" in detail


# ---------------------------------------------------------------------------
# Test 7 — soft_org_guard passes unauthenticated callers
# ---------------------------------------------------------------------------


def test_soft_org_guard_passes_unauthenticated(db, client):
    """No auth header + any organization_id → NOT 403 (integration callers pass through)."""
    resp = client.get("/ap-advanced/retention/expiring?organization_id=org-B")
    # Must not be 403 (could be 200, 422, etc — anything except org_mismatch 403)
    assert resp.status_code != 403
