"""Hard-purge behaviour for soft-deleted organizations.

The soft-delete tombstone (organizations.deleted_at) is meant to
trigger a real right-to-be-forgotten purge after a legal-hold
window. These tests lock in the three invariants that matter:

  1. The purge actually deletes rows from org-scoped tables.
  2. It never touches the append-only audit history (compliance).
  3. It never leaks across tenants — only the target org's rows go.

Data on other tenants surviving the sweep is the single most
important property here. If that ever broke, a scheduled purge
could wipe a live tenant's AP items.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture()
def fresh_db(monkeypatch):
    """Point ClearledgrDB at a throwaway SQLite file so the test is
    isolated from whatever the other tests or the dev DB are doing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", tmp.name)
    # Reset the module-level singleton so get_db() picks up the new path.
    import clearledgr.core.database as db_module
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    yield db
    db_module._DB_INSTANCE = None
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def test_purge_scopes_to_target_org_only(fresh_db):
    db = fresh_db
    # Two orgs, each with at least one AP item.
    db.ensure_organization("org_a", organization_name="A")
    db.ensure_organization("org_b", organization_name="B")
    item_a = db.create_ap_item({"organization_id": "org_a", "vendor_name": "Acme", "state": "received"})
    item_b = db.create_ap_item({"organization_id": "org_b", "vendor_name": "Contoso", "state": "received"})

    counts = db.purge_organization_data("org_a")

    # org_a's ap_item is gone…
    assert db.get_ap_item(item_a["id"]) is None
    # …but org_b's is untouched.
    assert db.get_ap_item(item_b["id"]) is not None
    # Per-table counts reflect that at least ap_items got purged.
    assert counts.get("ap_items", 0) >= 1


def test_purge_leaves_audit_events_intact(fresh_db):
    db = fresh_db
    db.ensure_organization("org_c", organization_name="C")
    item = db.create_ap_item({"organization_id": "org_c", "vendor_name": "Delta", "state": "received"})
    db.append_ap_audit_event({
        "event_type": "test_event",
        "actor_type": "system",
        "actor_id": "pytest",
        "ap_item_id": item["id"],
        "organization_id": "org_c",
    })

    # Sanity: the audit row exists before the purge.
    events_before = db.list_ap_audit_events(item["id"], limit=10)
    assert len(events_before) >= 1

    counts = db.purge_organization_data("org_c")

    # ap_items is gone from the purge result.
    assert counts.get("ap_items", 0) >= 1
    # audit_events was NOT in the purge surface (append-only trigger
    # would have aborted the delete anyway, but we prefer it never
    # being attempted — verified via the exclusion set).
    assert "audit_events" not in counts
    # Audit rows still queryable after the purge.
    events_after = db.list_ap_audit_events(item["id"], limit=10)
    assert len(events_after) >= len(events_before)


def test_list_org_scoped_tables_excludes_audit_surfaces(fresh_db):
    db = fresh_db
    tables = set(db.list_org_scoped_tables())
    # Audit tables must never be in the discovery result.
    assert "audit_events" not in tables
    assert "ap_policy_audit_events" not in tables
    # Organizations table is the tombstone anchor — also excluded.
    assert "organizations" not in tables
    # Sanity: at least one known tenant table IS present, so the
    # discovery isn't a no-op.
    assert "ap_items" in tables


def test_purge_empty_org_id_is_safe(fresh_db):
    db = fresh_db
    # Guard: no args, no accidents. Empty / whitespace / None all
    # short-circuit to an empty result and delete nothing.
    assert db.purge_organization_data("") == {}
    assert db.purge_organization_data("   ") == {}
    assert db.purge_organization_data(None) == {}
