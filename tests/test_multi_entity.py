"""Tests for multi-entity support (P0 pilot blocker).

Covers:
- Entity CRUD (create, get, list, update, soft-delete)
- Entity-scoped AP items
- Entity-scoped ERP connections
- Entity routing integration with build_worklist_item
- Backward compatibility (zero entities configured)
"""
from __future__ import annotations


from clearledgr.core.database import ClearledgrDB
from clearledgr.core.ap_entity_routing import (
    resolve_entity_routing,
    _db_entities_as_candidates,
)


# ------------------------------------------------------------------
# Entity Store CRUD
# ------------------------------------------------------------------


def test_create_entity(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(
        organization_id="org-1",
        name="Cowrywise Nigeria",
        code="NG",
        currency="NGN",
    )
    assert entity["id"].startswith("ENT-")
    assert entity["name"] == "Cowrywise Nigeria"
    assert entity["code"] == "NG"
    assert entity["default_currency"] == "NGN"
    assert entity["is_active"] is True
    assert entity["gl_mapping"] == {}
    assert entity["approval_rules"] == {}


def test_create_entity_with_gl_mapping_and_approval_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    gl = {"expenses": "7000", "cash": "1100"}
    rules = {"max_auto_approve": 5000, "require_po": True}
    entity = db.create_entity(
        organization_id="org-1",
        name="US Entity",
        code="US",
        gl_mapping=gl,
        approval_rules=rules,
    )
    assert entity["gl_mapping"] == gl
    assert entity["approval_rules"] == rules


def test_get_entity(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    created = db.create_entity(organization_id="org-1", name="Test Entity")
    fetched = db.get_entity(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["name"] == "Test Entity"


def test_get_entity_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))
    db.initialize()

    assert db.get_entity("nonexistent") is None


def test_list_entities(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    db.create_entity(organization_id="org-1", name="Nigeria", code="NG")
    db.create_entity(organization_id="org-1", name="United States", code="US")
    db.create_entity(organization_id="org-2", name="Other Org Entity", code="OT")

    org1_entities = db.list_entities("org-1")
    assert len(org1_entities) == 2
    names = {e["name"] for e in org1_entities}
    assert names == {"Nigeria", "United States"}

    org2_entities = db.list_entities("org-2")
    assert len(org2_entities) == 1
    assert org2_entities[0]["name"] == "Other Org Entity"


def test_list_entities_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))
    db.initialize()

    assert db.list_entities("org-no-entities") == []


def test_get_entity_by_code(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    db.create_entity(organization_id="org-1", name="Nigeria", code="NG")
    result = db.get_entity_by_code("org-1", "NG")
    assert result is not None
    assert result["name"] == "Nigeria"

    assert db.get_entity_by_code("org-1", "XX") is None


def test_update_entity(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="Old Name", code="OLD")
    assert db.update_entity(entity["id"], name="New Name", code="NEW")
    updated = db.get_entity(entity["id"])
    assert updated["name"] == "New Name"
    assert updated["code"] == "NEW"


def test_update_entity_gl_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="Entity")
    db.update_entity(entity["id"], gl_mapping={"expenses": "8000"})
    updated = db.get_entity(entity["id"])
    assert updated["gl_mapping"] == {"expenses": "8000"}


def test_update_entity_rejects_unknown_columns(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="Entity")
    # Unknown column should be silently ignored
    result = db.update_entity(entity["id"], evil_column="drop table")
    assert result is False


def test_delete_entity_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="To Delete", code="DEL")
    assert db.delete_entity(entity["id"])

    # Not visible in default list
    assert db.list_entities("org-1") == []

    # Still exists when including inactive
    all_entities = db.list_entities("org-1", include_inactive=True)
    assert len(all_entities) == 1
    assert all_entities[0]["is_active"] is False


# ------------------------------------------------------------------
# AP Items with entity_id
# ------------------------------------------------------------------


def test_ap_item_with_entity_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="NG Entity", code="NG")

    item = db.create_ap_item({
        "state": "received",
        "organization_id": "org-1",
        "entity_id": entity["id"],
        "vendor_name": "Vendor A",
        "amount": 100.0,
    })
    assert item["entity_id"] == entity["id"]


def test_ap_item_without_entity_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    # Should work fine without entity_id (backward compatible)
    item = db.create_ap_item({
        "state": "received",
        "organization_id": "org-1",
        "vendor_name": "Vendor B",
        "amount": 50.0,
    })
    assert item.get("entity_id") is None


def test_update_ap_item_entity_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="US Entity", code="US")
    item = db.create_ap_item({
        "state": "received",
        "organization_id": "org-1",
        "vendor_name": "Vendor C",
        "amount": 200.0,
    })
    assert item.get("entity_id") is None

    db.update_ap_item(item["id"], entity_id=entity["id"])
    updated = db.get_ap_item(item["id"])
    assert updated["entity_id"] == entity["id"]


# ------------------------------------------------------------------
# Entity-scoped ERP connections
# ------------------------------------------------------------------


def test_entity_erp_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    entity = db.create_entity(organization_id="org-1", name="NG Entity", code="NG")

    conn_id = db.save_erp_connection_for_entity(
        organization_id="org-1",
        erp_type="quickbooks",
        entity_id=entity["id"],
        realm_id="ng-realm-123",
    )
    assert conn_id.startswith("ERP-")

    conn = db.get_erp_connection_by_id(conn_id)
    assert conn is not None
    assert conn["erp_type"] == "quickbooks"
    assert conn["realm_id"] == "ng-realm-123"
    assert conn["entity_id"] == entity["id"]


def test_get_erp_connection_by_id_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))
    db.initialize()

    assert db.get_erp_connection_by_id("nonexistent") is None


# ------------------------------------------------------------------
# Entity routing integration
# ------------------------------------------------------------------


def test_resolve_entity_routing_with_db_entities():
    db_entities = [
        {"id": "ENT-1", "code": "NG", "name": "Cowrywise Nigeria"},
        {"id": "ENT-2", "code": "US", "name": "Cowrywise US"},
    ]
    result = resolve_entity_routing(
        metadata={},
        item={"entity_id": "ENT-1"},
        db_entities=db_entities,
    )
    assert result["status"] == "resolved"
    assert result["selected"]["entity_id"] == "ENT-1"
    assert len(result["candidates"]) == 2


def test_resolve_entity_routing_needs_review_multiple_db_entities():
    db_entities = [
        {"id": "ENT-1", "code": "NG", "name": "Nigeria"},
        {"id": "ENT-2", "code": "US", "name": "United States"},
    ]
    result = resolve_entity_routing(
        metadata={},
        item={},
        db_entities=db_entities,
    )
    assert result["status"] == "needs_review"
    assert len(result["candidates"]) == 2


def test_resolve_entity_routing_single_db_entity_auto_selects():
    db_entities = [
        {"id": "ENT-1", "code": "NG", "name": "Cowrywise Nigeria"},
    ]
    result = resolve_entity_routing(
        metadata={},
        item={},
        db_entities=db_entities,
    )
    # Single entity should auto-select
    assert result["status"] == "resolved"
    assert result["selected"]["entity_id"] == "ENT-1"


def test_resolve_entity_routing_no_db_entities_backward_compatible():
    # Zero entities = behave exactly as before
    result = resolve_entity_routing(
        metadata={},
        item={},
        db_entities=[],
    )
    assert result["status"] == "not_needed"
    assert result["candidates"] == []
    assert result["selected"] == {}


def test_resolve_entity_routing_no_db_entities_none():
    result = resolve_entity_routing(
        metadata={},
        item={},
        db_entities=None,
    )
    assert result["status"] == "not_needed"


def test_db_entities_as_candidates():
    db_entities = [
        {"id": "ENT-1", "code": "NG", "name": "Nigeria"},
        {"id": "ENT-2", "code": "US", "name": "United States"},
    ]
    candidates = _db_entities_as_candidates(db_entities)
    assert len(candidates) == 2
    assert candidates[0]["entity_id"] == "ENT-1"
    assert candidates[0]["entity_code"] == "NG"
    assert candidates[0]["entity_name"] == "Nigeria"


def test_db_entities_as_candidates_empty():
    assert _db_entities_as_candidates([]) == []


# ------------------------------------------------------------------
# Entity + ERP connection resolution (erp_router integration)
# ------------------------------------------------------------------


def test_erp_connection_entity_resolution(tmp_path, monkeypatch):
    """Entity-specific ERP connection takes priority over org default."""
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    # Create org-level default connection
    db.save_erp_connection(
        organization_id="org-1",
        erp_type="quickbooks",
        realm_id="org-default-realm",
    )

    # Create entity with its own connection
    entity = db.create_entity(organization_id="org-1", name="NG Entity", code="NG")
    conn_id = db.save_erp_connection_for_entity(
        organization_id="org-1",
        erp_type="xero",
        entity_id=entity["id"],
        tenant_id="ng-xero-tenant",
    )
    db.update_entity(entity["id"], erp_connection_id=conn_id)

    from clearledgr.integrations.erp_router import get_erp_connection

    # Without entity_id, should get org default (quickbooks)
    monkeypatch.setattr("clearledgr.integrations.erp_router._get_db", lambda: db)
    org_conn = get_erp_connection("org-1")
    assert org_conn is not None
    assert org_conn.type == "quickbooks"
    assert org_conn.realm_id == "org-default-realm"

    # With entity_id, should get entity-specific (xero)
    entity_conn = get_erp_connection("org-1", entity_id=entity["id"])
    assert entity_conn is not None
    assert entity_conn.type == "xero"
    assert entity_conn.tenant_id == "ng-xero-tenant"


def test_erp_connection_entity_fallback_to_org(tmp_path, monkeypatch):
    """Entity without dedicated ERP connection falls back to org default."""
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    db.save_erp_connection(
        organization_id="org-1",
        erp_type="quickbooks",
        realm_id="org-realm",
    )
    entity = db.create_entity(organization_id="org-1", name="NG Entity", code="NG")
    # Entity has no erp_connection_id set

    from clearledgr.integrations.erp_router import get_erp_connection

    monkeypatch.setattr("clearledgr.integrations.erp_router._get_db", lambda: db)
    conn = get_erp_connection("org-1", entity_id=entity["id"])
    assert conn is not None
    assert conn.type == "quickbooks"
    assert conn.realm_id == "org-realm"


# ------------------------------------------------------------------
# Unique constraint on (organization_id, code)
# ------------------------------------------------------------------


def test_entity_unique_code_per_org(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    db.create_entity(organization_id="org-1", name="Entity A", code="NG")
    try:
        db.create_entity(organization_id="org-1", name="Entity B", code="NG")
        assert False, "Should have raised due to unique constraint"
    except Exception:
        pass  # Expected: UNIQUE constraint violation

    # Same code in different org should work
    entity = db.create_entity(organization_id="org-2", name="Entity C", code="NG")
    assert entity["code"] == "NG"


# ------------------------------------------------------------------
# Backward compatibility: everything works with zero entities
# ------------------------------------------------------------------


def test_backward_compat_no_entities(tmp_path, monkeypatch):
    """Full backward compatibility: no entities configured, all AP flows work."""
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-key")
    db = ClearledgrDB(str(tmp_path / "entity.db"))

    # Create AP item without entity
    item = db.create_ap_item({
        "state": "received",
        "organization_id": "org-1",
        "vendor_name": "Vendor X",
        "amount": 999.0,
    })
    assert item.get("entity_id") is None

    # Entity list is empty
    assert db.list_entities("org-1") == []

    # Routing returns not_needed
    result = resolve_entity_routing(
        metadata={},
        item={"organization_id": "org-1"},
        db_entities=[],
    )
    assert result["status"] == "not_needed"
