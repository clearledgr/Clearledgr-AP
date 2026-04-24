from __future__ import annotations

from clearledgr.core import database as db_module
from clearledgr.core.launch_controls import set_ga_readiness, set_rollback_controls
from clearledgr.services.erp_readiness import evaluate_erp_connector_readiness


def _db(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()
    return db

def test_connector_readiness_passes_for_enabled_connector_with_completed_checklist(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db.save_erp_connection(
        organization_id="default",
        erp_type="quickbooks",
        access_token="token",
        refresh_token="refresh",
        realm_id="realm-1",
    )
    set_ga_readiness(
        "default",
        {
            "connector_checklists": {
                "quickbooks": {"completed": True, "signed_off": True},
            }
        },
        updated_by="owner-1",
        db=db,
    )

    readiness = evaluate_erp_connector_readiness("default", db=db)
    summary = readiness["summary"]
    quickbooks = next(row for row in readiness["connectors"] if row["erp_type"] == "quickbooks")

    assert summary["status"] == "pass"
    assert summary["configured_connectors_total"] == 1
    assert summary["enabled_connectors_total"] == 1
    assert summary["enabled_connectors_ready"] == 1
    assert summary["enabled_readiness_rate"] == 1.0
    assert quickbooks["ready"] is True
    assert quickbooks["readiness_status"] == "ready"


def test_connector_readiness_blocks_when_configured_connector_is_rollback_disabled(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db.save_erp_connection(
        organization_id="default",
        erp_type="quickbooks",
        access_token="token",
        refresh_token="refresh",
        realm_id="realm-1",
    )
    set_ga_readiness(
        "default",
        {
            "connector_checklists": {
                "quickbooks": {"completed": True, "signed_off": True},
            }
        },
        updated_by="owner-1",
        db=db,
    )
    set_rollback_controls(
        "default",
        {"erp_connectors_disabled": ["quickbooks"], "reason": "incident"},
        updated_by="owner-1",
        db=db,
    )

    readiness = evaluate_erp_connector_readiness("default", db=db)
    summary = readiness["summary"]
    quickbooks = next(row for row in readiness["connectors"] if row["erp_type"] == "quickbooks")

    assert summary["status"] == "blocked"
    assert summary["enabled_connectors_total"] == 0
    assert "quickbooks:disabled_by_rollback" in summary["blocked_reasons"]
    assert quickbooks["rollback_blocked"] is True
    assert quickbooks["readiness_status"] == "disabled_by_rollback"
