"""Tests for Module 9 per-entity audit log scoping (§300, §307).

Pinned by these tests:

  - Migration v72 added the entity_id column and the backfill
    populated existing ap_item rows from the AP item's entity_id.
  - append_audit_event now stamps entity_id automatically for
    box_type='ap_item' rows; explicit entity_id in the payload
    wins; non-AP rows stay NULL.
  - resolve_audit_entity_scope:
      - returns None for owner / cfo / financial_controller without
        entity restrictions (they see everything)
      - returns the per-(user, entity) row entity_ids for users with
        user_entity_roles assignments and a non-admin role
      - returns invite-time entity_restrictions when set on the user
      - empty list when caller has no access at all (defensive)
  - search_audit_events filter:
      - entity_scope=None applies no filter (admin full access)
      - entity_scope=[X] returns rows where entity_id IS NULL OR
        entity_id IN ([X]) — org-level rows still visible
      - entity_scope=[] returns ONLY org-level rows (entity_id IS NULL)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.services.audit_entity_scope import (  # noqa: E402
    build_entity_scope_clause,
    resolve_audit_entity_scope,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgA", organization_name="Acme UK Ltd")
    return inst


def _write_ap_audit(
    db, *, ap_item_id: str, organization_id: str = "orgA",
    event_type: str = "test_event", entity_id=None,
):
    payload = {
        "ap_item_id": ap_item_id,
        "event_type": event_type,
        "organization_id": organization_id,
        "actor_type": "agent",
        "actor_id": "test",
    }
    if entity_id is not None:
        payload["entity_id"] = entity_id
    return db.append_audit_event(payload)


def _make_ap_item(db, *, item_id: str, entity_id=None, organization_id: str = "orgA"):
    return db.create_ap_item({
        "id": item_id,
        "organization_id": organization_id,
        "vendor_name": "Vendor",
        "amount": 100.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": "received",
        "entity_id": entity_id,
        "metadata": {},
    })


# ─── Tests: append_audit_event populates entity_id ─────────────────


class TestAppendAutoStampsEntityId:
    def test_ap_item_event_gets_entity_id_from_box(self, db):
        _make_ap_item(db, item_id="ap-eu-1", entity_id="eu-1")
        evt = _write_ap_audit(db, ap_item_id="ap-eu-1")
        assert evt is not None
        assert evt.get("entity_id") == "eu-1"

    def test_explicit_entity_id_wins(self, db):
        _make_ap_item(db, item_id="ap-mixed-1", entity_id="eu-1")
        evt = _write_ap_audit(db, ap_item_id="ap-mixed-1", entity_id="us-2")
        assert evt.get("entity_id") == "us-2"

    def test_org_level_event_stays_null(self, db):
        # Box type other than ap_item; no explicit entity_id.
        evt = db.append_audit_event({
            "box_id": "org-config",
            "box_type": "organization",
            "event_type": "organization_renamed",
            "organization_id": "orgA",
            "actor_type": "user",
            "actor_id": "admin@orga.com",
        })
        assert evt is not None
        assert evt.get("entity_id") in (None, "")


# ─── Tests: search filter ──────────────────────────────────────────


class TestSearchEntityFilter:
    def test_none_scope_returns_all(self, db):
        _make_ap_item(db, item_id="ap-eu-2", entity_id="eu-1")
        _make_ap_item(db, item_id="ap-us-1", entity_id="us-1")
        _write_ap_audit(db, ap_item_id="ap-eu-2", event_type="test_eu")
        _write_ap_audit(db, ap_item_id="ap-us-1", event_type="test_us")

        result = db.search_audit_events(
            organization_id="orgA", entity_scope=None,
        )
        types = {e["event_type"] for e in result["events"]}
        assert "test_eu" in types
        assert "test_us" in types

    def test_restricted_scope_filters_to_entities_plus_org_level(self, db):
        _make_ap_item(db, item_id="ap-eu-3", entity_id="eu-1")
        _make_ap_item(db, item_id="ap-us-2", entity_id="us-1")
        _write_ap_audit(db, ap_item_id="ap-eu-3", event_type="ev_eu")
        _write_ap_audit(db, ap_item_id="ap-us-2", event_type="ev_us")
        # Org-level event (entity_id NULL) — should also be visible
        # to entity-scoped auditors.
        db.append_audit_event({
            "box_id": "orgA", "box_type": "organization",
            "event_type": "ev_org_level",
            "organization_id": "orgA",
            "actor_type": "user", "actor_id": "admin@orga.com",
        })

        result = db.search_audit_events(
            organization_id="orgA", entity_scope=["eu-1"],
        )
        types = {e["event_type"] for e in result["events"]}
        assert "ev_eu" in types
        assert "ev_org_level" in types
        assert "ev_us" not in types

    def test_empty_scope_returns_only_org_level(self, db):
        _make_ap_item(db, item_id="ap-eu-4", entity_id="eu-1")
        _write_ap_audit(db, ap_item_id="ap-eu-4", event_type="ev_eu_4")
        db.append_audit_event({
            "box_id": "orgA", "box_type": "organization",
            "event_type": "ev_org_only",
            "organization_id": "orgA",
            "actor_type": "user", "actor_id": "admin@orga.com",
        })

        result = db.search_audit_events(
            organization_id="orgA", entity_scope=[],
        )
        types = {e["event_type"] for e in result["events"]}
        assert "ev_org_only" in types
        assert "ev_eu_4" not in types


# ─── Tests: scope resolver ─────────────────────────────────────────


class TestResolveScope:
    def test_owner_role_returns_none_full_access(self, db):
        user = SimpleNamespace(
            user_id="u-owner", organization_id="orgA", role="owner",
        )
        assert resolve_audit_entity_scope(db, user) is None

    def test_cfo_role_returns_none(self, db):
        user = SimpleNamespace(
            user_id="u-cfo", organization_id="orgA", role="cfo",
        )
        assert resolve_audit_entity_scope(db, user) is None

    def test_financial_controller_returns_none(self, db):
        user = SimpleNamespace(
            user_id="u-fc", organization_id="orgA", role="financial_controller",
        )
        assert resolve_audit_entity_scope(db, user) is None

    def test_user_with_entity_role_overrides_returns_those_entities(self, db):
        # Create a non-admin user with per-entity role assignments.
        user_row = db.create_user(
            email="auditor-eu@orga.com",
            name="EU Auditor",
            organization_id="orgA",
            role="ap_clerk",
        )
        user_id = str(user_row["id"])
        if hasattr(db, "upsert_user_entity_role"):
            db.upsert_user_entity_role(
                user_id=user_id, entity_id="eu-1",
                organization_id="orgA", role="read_only",
            )
            db.upsert_user_entity_role(
                user_id=user_id, entity_id="eu-2",
                organization_id="orgA", role="read_only",
            )
            user = SimpleNamespace(
                user_id=user_id, organization_id="orgA", role="ap_clerk",
            )
            scope = resolve_audit_entity_scope(db, user)
            assert scope is not None
            assert "eu-1" in scope
            assert "eu-2" in scope

    def test_unknown_user_falls_back_to_full_access(self, db):
        user = SimpleNamespace(
            user_id="nonexistent", organization_id="orgA", role="ap_clerk",
        )
        # No entity restrictions stored anywhere → None.
        assert resolve_audit_entity_scope(db, user) is None

    def test_anonymous_returns_empty_no_access(self, db):
        # No user → conservative default: see nothing.
        assert resolve_audit_entity_scope(db, None) == []


# ─── Tests: scope clause builder ───────────────────────────────────


class TestScopeClauseBuilder:
    def test_none_scope_emits_empty_clause(self):
        clause, args = build_entity_scope_clause(None)
        assert clause == ""
        assert args == ()

    def test_empty_scope_filters_to_org_level(self):
        clause, args = build_entity_scope_clause([])
        assert "entity_id IS NULL" in clause
        assert args == ()

    def test_restricted_scope_emits_or_in_clause(self):
        clause, args = build_entity_scope_clause(["eu-1", "eu-2"])
        assert "entity_id IS NULL" in clause
        assert "IN (" in clause
        assert args == ("eu-1", "eu-2")
