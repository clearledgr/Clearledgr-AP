from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.ap_item_contracts import MergeItemsRequest, ResubmitRejectedItemRequest
from clearledgr.api.ap_items_action_routes import merge_ap_items, resubmit_rejected_item
from clearledgr.api.ap_items_read_routes import get_ap_item_context
from clearledgr.core import database as db_module
from clearledgr.services.ap_item_service import build_worklist_item


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()
    return db


def _create_ap_item(db, *, item_id: str, thread_id: str, state: str = "needs_approval") -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": thread_id,
            "message_id": f"msg-{thread_id}",
            "subject": f"Invoice {item_id}",
            "sender": "billing@example.com",
            "vendor_name": "Acme",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{item_id}",
            "state": state,
            "confidence": 0.99,
            "organization_id": "default",
            "metadata": {},
        }
    )


def _parse_metadata(row: dict) -> dict:
    raw = row.get("metadata")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    return {}


def _mock_user(*, user_id: str = "test-user", organization_id: str = "default"):
    return SimpleNamespace(
        email=None,
        user_id=user_id,
        organization_id=organization_id,
        role="user",
    )


def test_merge_ap_items_uses_metadata_linkage_without_illegal_state(db):
    target = _create_ap_item(db, item_id="AP-TARGET-1", thread_id="thread-target")
    source = _create_ap_item(db, item_id="AP-SOURCE-1", thread_id="thread-source")

    db.link_ap_item_source(
        {
            "ap_item_id": source["id"],
            "source_type": "portal",
            "source_ref": "portal-doc-1",
            "subject": "Portal Invoice",
            "sender": "portal@example.com",
            "detected_at": source.get("created_at"),
            "metadata": {"kind": "portal_invoice"},
        }
    )

    response = asyncio.run(merge_ap_items(
        target["id"],
        MergeItemsRequest(source_ap_item_id=source["id"], actor_id="user-1", reason="duplicate_invoice"),
        _user=_mock_user(user_id="user-1", organization_id="default"),
    ))

    assert response["status"] == "merged"
    assert response["target_ap_item_id"] == target["id"]
    assert response["source_ap_item_id"] == source["id"]
    assert response["moved_sources"] >= 1

    source_after = db.get_ap_item(source["id"])
    assert source_after is not None
    # Source remains in a canonical AP state; merge semantics are tracked via metadata/audit.
    assert source_after["state"] == "needs_approval"

    source_meta = _parse_metadata(source_after)
    assert source_meta["merged_into"] == target["id"]
    assert source_meta["merge_status"] == "merged_source"
    assert source_meta["merge_reason"] == "duplicate_invoice"
    assert source_meta["merged_by"] == "user-1"
    assert source_meta.get("suppressed_from_worklist") is True

    source_worklist = build_worklist_item(db, source_after)
    assert source_worklist["is_merged_source"] is True
    assert source_worklist["merged_into"] == target["id"]
    assert source_worklist["next_action"] == "none"

    target_sources = db.list_ap_item_sources(target["id"])
    moved_refs = {(row.get("source_type"), row.get("source_ref")) for row in target_sources}
    assert ("portal", "portal-doc-1") in moved_refs

    target_events = [e.get("event_type") for e in db.list_ap_audit_events(target["id"])]
    source_events = [e.get("event_type") for e in db.list_ap_audit_events(source["id"])]
    assert "ap_item_merged" in target_events
    assert "ap_item_merged_into" in source_events


def test_audit_events_table_is_append_only(db):
    item = _create_ap_item(db, item_id="AP-AUDIT-1", thread_id="thread-audit")
    event = db.append_audit_event(
        {
            "ap_item_id": item["id"],
            "event_type": "test_audit_event",
            "actor_type": "system",
            "actor_id": "test-suite",
            "organization_id": "default",
            "idempotency_key": "audit-append-only:test",
            "metadata": {"ok": True},
        }
    )
    assert event is not None

    # append-only trigger raises a dialect-specific exception:
    # sqlite3.DatabaseError on SQLite, psycopg.errors.RaiseException on PG.
    # Catch Exception so the test is engine-agnostic.
    with pytest.raises(Exception):
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                (
                    "UPDATE audit_events SET event_type = %s WHERE id = %s"
                ),
                ("mutated_event", event["id"]),
            )
            conn.commit()

    with pytest.raises(Exception):
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM audit_events WHERE id = %s",
                (event["id"],),
            )
            conn.commit()

    persisted = db.get_ap_audit_event(event["id"])
    assert persisted is not None
    assert persisted["event_type"] == "test_audit_event"


def test_rejected_item_resubmission_creates_new_item_with_supersession_linkage(db):
    rejected = _create_ap_item(db, item_id="AP-REJ-1", thread_id="thread-rej", state="rejected")
    db.link_ap_item_source(
        {
            "ap_item_id": rejected["id"],
            "source_type": "portal",
            "source_ref": "portal-resubmit-1",
            "subject": "Portal invoice",
            "sender": "portal@example.com",
            "metadata": {"kind": "portal_invoice"},
        }
    )

    response = asyncio.run(resubmit_rejected_item(
        rejected["id"],
        ResubmitRejectedItemRequest(
            actor_id="ap-user-1",
            reason="corrected_vendor_and_amount",
            message_id="msg-thread-rej-v2",
            thread_id="thread-rej-v2",
            amount=125.50,
        ),
        _user=_mock_user(user_id="ap-user-1", organization_id="default"),
    ))

    assert response["status"] == "resubmitted"
    new_ap_item_id = response["new_ap_item_id"]
    assert new_ap_item_id != rejected["id"]
    assert response["linkage"]["supersedes_ap_item_id"] == rejected["id"]
    assert response["linkage"]["supersedes_invoice_key"] == rejected["invoice_key"]
    assert response["linkage"]["superseded_by_ap_item_id"] == new_ap_item_id
    assert response["linkage"]["resubmission_reason"] == "corrected_vendor_and_amount"
    assert response["copied_sources"] >= 1

    original_after = db.get_ap_item(rejected["id"])
    assert original_after is not None
    assert original_after["state"] == "rejected"
    assert original_after["superseded_by_ap_item_id"] == new_ap_item_id

    new_item = db.get_ap_item(new_ap_item_id)
    assert new_item is not None
    assert new_item["state"] == "received"
    assert new_item["supersedes_ap_item_id"] == rejected["id"]
    assert new_item["supersedes_invoice_key"] == rejected["invoice_key"]
    assert new_item["resubmission_reason"] == "corrected_vendor_and_amount"
    assert new_item["message_id"] == "msg-thread-rej-v2"
    assert float(new_item["amount"]) == 125.50
    assert "resub:" in str(new_item["invoice_key"])

    original_worklist = build_worklist_item(db, original_after)
    new_worklist = build_worklist_item(db, new_item)
    assert original_worklist["superseded_by_ap_item_id"] == new_ap_item_id
    assert original_worklist["next_action"] == "none"
    assert new_worklist["supersedes_ap_item_id"] == rejected["id"]
    assert new_worklist["is_resubmission"] is True

    new_context = get_ap_item_context(new_ap_item_id, refresh=True)
    supersession = new_context.get("supersession") or {}
    assert supersession["supersedes_ap_item_id"] == rejected["id"]
    assert supersession["supersedes_invoice_key"] == rejected["invoice_key"]
    assert supersession["resubmission_reason"] == "corrected_vendor_and_amount"

    source_events = [e.get("event_type") for e in db.list_ap_audit_events(rejected["id"])]
    new_events = [e.get("event_type") for e in db.list_ap_audit_events(new_ap_item_id)]
    assert "ap_item_resubmitted" in source_events
    assert "ap_item_resubmission_created" in new_events


def test_resubmission_requires_rejected_state(db):
    item = _create_ap_item(db, item_id="AP-NOT-REJECTED-1", thread_id="thread-not-rej", state="needs_approval")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(resubmit_rejected_item(
            item["id"],
            ResubmitRejectedItemRequest(actor_id="ap-user-1", reason="should_fail"),
            _user=_mock_user(user_id="ap-user-1", organization_id="default"),
        ))
    assert exc.value.status_code == 400
    assert exc.value.detail == "resubmission_requires_rejected_state"


def test_illegal_state_transition_is_rejected_and_audited(db):
    item = _create_ap_item(db, item_id="AP-ILLEGAL-1", thread_id="thread-illegal", state="received")

    with pytest.raises(ValueError):
        db.update_ap_item(
            item["id"],
            state="posted_to_erp",
            _actor_type="user",
            _actor_id="tester@example.com",
            _source="unit_test",
            _correlation_id="corr-illegal-1",
            _workflow_id="ap_runtime",
            _run_id="run-illegal-1",
            _decision_reason="test_illegal_transition",
        )

    persisted = db.get_ap_item(item["id"])
    assert persisted is not None
    assert persisted["state"] == "received"

    events = db.list_ap_audit_events(item["id"])
    rejected = [e for e in events if e.get("event_type") == "state_transition_rejected"]
    assert rejected, "Expected rejected transition audit event"
    event = rejected[-1]
    assert event["from_state"] == "received"
    assert event["to_state"] == "posted_to_erp"
    assert event["actor_id"] == "tester@example.com"
    assert event["source"] == "unit_test"
    assert event["correlation_id"] == "corr-illegal-1"
    assert event["workflow_id"] == "ap_runtime"
    assert event["run_id"] == "run-illegal-1"
    assert event["decision_reason"] == "test_illegal_transition"
    payload = event.get("payload_json") or {}
    assert "error" in payload


def test_postgres_append_only_guard_ddl_is_installed_without_live_db(db):
    """The append-only DDL uses ``CREATE OR REPLACE TRIGGER`` (Postgres
    14+, atomic, race-free) instead of the prior DROP-then-CREATE
    pattern. The latter took ``AccessExclusiveLock`` on the table and
    deadlocked when two gunicorn workers booted in parallel — the
    rewrite is documented in
    ``ClearledgrDB._install_audit_append_only_guards``. This test
    verifies the new shape so a future refactor can't silently
    regress to the deadlock-prone DROP+CREATE.
    """
    class _FakeCursor:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, sql, params=None):  # noqa: ANN001
            self.statements.append(str(sql).strip())
            return None

    cur = _FakeCursor()
    db._install_audit_append_only_guards(cur)

    joined = "\n".join(cur.statements)
    assert "CREATE OR REPLACE FUNCTION clearledgr_prevent_append_only_mutation" in joined
    # CREATE OR REPLACE TRIGGER is atomic and avoids the AccessExclusiveLock
    # deadlock the prior DROP+CREATE pattern caused; both audit tables get
    # an UPDATE-blocker and a DELETE-blocker.
    assert "CREATE OR REPLACE TRIGGER trg_audit_events_no_update" in joined
    assert "CREATE OR REPLACE TRIGGER trg_audit_events_no_delete" in joined
    assert "BEFORE UPDATE ON audit_events" in joined
    assert "BEFORE DELETE ON audit_events" in joined
    assert "CREATE OR REPLACE TRIGGER trg_ap_policy_audit_events_no_update" in joined
    assert "CREATE OR REPLACE TRIGGER trg_ap_policy_audit_events_no_delete" in joined
    # No DROP TRIGGER calls — the rewrite eliminated them deliberately.
    assert "DROP TRIGGER" not in joined
