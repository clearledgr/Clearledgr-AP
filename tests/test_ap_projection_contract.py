from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.api.ap_items import build_worklist_item  # noqa: E402
from clearledgr.core import database as db_module  # noqa: E402


def _item_payload(
    item_id: str,
    *,
    state: str = "validated",
    vendor_name: str = "Acme",
    metadata: dict | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "id": item_id,
        "invoice_key": f"inv-{item_id}",
        "thread_id": f"thread-{item_id}",
        "message_id": f"msg-{item_id}",
        "subject": f"Invoice {item_id}",
        "sender": "billing@example.com",
        "vendor_name": vendor_name,
        "amount": 125.0,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "state": state,
        "confidence": 0.97,
        "organization_id": "default",
        "metadata": metadata or {},
    }
    if extra:
        payload.update(extra)
    return payload


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "ap-projection-contract.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


@pytest.mark.parametrize(
    ("name", "payload", "org_settings", "expected"),
    [
        (
            "entity_review_requires_resolution",
            _item_payload(
                "projection-entity-review",
                state="needs_approval",
                metadata={
                    "approval_requested_at": "2026-03-18T08:00:00+00:00",
                    "approval_sent_to": ["approver@clearledgr.com"],
                    "approval_escalation_count": 1,
                    "entity_candidates": [
                        {"entity_code": "US-01", "entity_name": "Acme US"},
                        {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                    ],
                },
            ),
            None,
            {
                "entity_routing_status": "needs_review",
                "next_action": "resolve_entity_route",
                "workflow_paused_reason": None,
                "pipeline_blocker_kinds": {"entity"},
                "approval_sla_breached": True,
            },
        ),
        (
            "org_rule_resolves_entity_for_validated_invoice",
            _item_payload(
                "projection-entity-resolved",
                state="validated",
                extra={"sender": "billing@ghana.vendor.example"},
            ),
            {
                "entity_routing": {
                    "entities": [
                        {"entity_code": "US-01", "entity_name": "Acme US"},
                        {"entity_code": "GH-01", "entity_name": "Acme Ghana"},
                    ],
                    "rules": [
                        {
                            "entity_code": "GH-01",
                            "sender_domains": ["ghana.vendor.example"],
                            "currencies": ["USD"],
                        }
                    ],
                }
            },
            {
                "entity_routing_status": "resolved",
                "next_action": "route_for_approval",
                "workflow_paused_reason": None,
                "pipeline_blocker_kinds": set(),
                "approval_sla_breached": None,
            },
        ),
        (
            "erp_connector_failure_surfaces_specific_pause_reason",
            _item_payload(
                "projection-erp-blocked",
                state="failed_post",
                metadata={"erp_connector_available": False},
                extra={"exception_code": "erp_not_connected"},
            ),
            None,
            {
                "entity_routing_status": "not_needed",
                "next_action": "retry_post",
                "workflow_paused_reason": "Connect an ERP before this invoice can be posted.",
                "pipeline_blocker_kinds": {"exception"},
                "approval_sla_breached": None,
            },
        ),
    ],
)
def test_ap_projection_contract_matrix(db, name, payload, org_settings, expected):
    if org_settings:
        db.ensure_organization("default", organization_name="default")
        db.update_organization("default", settings=org_settings)

    item = db.create_ap_item(payload)
    projected = build_worklist_item(db, item)

    assert projected["entity_routing_status"] == expected["entity_routing_status"], name
    assert projected["next_action"] == expected["next_action"], name
    assert projected.get("workflow_paused_reason") == expected["workflow_paused_reason"], name
    assert {row["kind"] for row in projected["pipeline_blockers"]} >= expected["pipeline_blocker_kinds"], name

    approval_followup = projected.get("approval_followup") if isinstance(projected.get("approval_followup"), dict) else {}
    if expected["approval_sla_breached"] is None:
        assert approval_followup.get("sla_breached") in {None, False}, name
    else:
        assert approval_followup.get("sla_breached") is expected["approval_sla_breached"], name
