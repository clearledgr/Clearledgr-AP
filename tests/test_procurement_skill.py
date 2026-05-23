"""ProcurementFinanceSkill + tiered thresholds + seed strategy.

Covers the agent's action interface for the purchase_order BoxType:
create/submit/approve through the skill, the tiered approval autonomy
gate, illegal-transition blocking, and the seed-strategy creation path.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.procurement_thresholds import (  # noqa: E402
    ProcurementThresholds,
    evaluate_po_approval,
)
from solden.services.finance_agent_runtime import FinanceAgentRuntime  # noqa: E402
from solden.services.finance_skills.procurement_skill import (  # noqa: E402
    ProcurementFinanceSkill,
)

ORG = "orgProcSkill"


@pytest.fixture()
def runtime():
    db = db_module.get_db()
    db.initialize()
    db.ensure_organization(ORG, organization_name=ORG)
    return FinanceAgentRuntime(
        organization_id=ORG, actor_id="agent@test", actor_email="agent@test", db=db,
    )


def test_threshold_tiers():
    cfg = ProcurementThresholds(auto_approve_ceiling=1000, dual_approval_above=25000)
    assert evaluate_po_approval(500, cfg).tier == "auto"
    assert evaluate_po_approval(500, cfg).auto_approvable is True
    assert evaluate_po_approval(5000, cfg).tier == "single"
    assert evaluate_po_approval(50000, cfg).tier == "dual"
    assert evaluate_po_approval(50000, cfg).requires_dual_approval is True


def test_skill_create_submit_approve(runtime):
    skill = ProcurementFinanceSkill()
    res = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Acme", "total_amount": 500.0},
    ))
    assert res["status"] == "created"
    po_id = res["po_id"]
    assert res["state"] == "draft"

    res2 = asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    assert res2["status"] == "ok" and res2["state"] == "pending_approval"

    res3 = asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))
    assert res3["status"] == "ok" and res3["state"] == "approved"


def test_skill_autonomy_gate_blocks_large_po(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Acme", "total_amount": 50000.0},  # above dual_approval_above default
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    out = asyncio.run(skill.execute(
        runtime, "approve_purchase_order", {"po_id": po_id, "autonomous": True},
    ))
    assert out["status"] == "blocked"
    assert "autonomy_gate_blocked" in out["policy_precheck"]["reason_codes"]


def test_skill_small_po_auto_approvable(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Acme", "total_amount": 200.0},  # below auto_approve_ceiling
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    out = asyncio.run(skill.execute(
        runtime, "approve_purchase_order", {"po_id": po_id, "autonomous": True},
    ))
    assert out["status"] == "ok" and out["state"] == "approved"


def test_skill_illegal_transition_blocked(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Acme", "total_amount": 500.0},
    ))
    po_id = created["po_id"]
    # approve a draft (must pass through pending_approval first) -> illegal
    out = asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))
    assert out["status"] == "blocked"
    assert "illegal_transition" in out["policy_precheck"]["reason_codes"]


def test_skill_registered_on_runtime(runtime):
    assert "approve_purchase_order" in runtime.supported_intents
    assert "create_purchase_order" in runtime.supported_intents


def test_seed_strategy_creates_po(runtime):
    box = runtime.seed_box("purchase_order", {"vendor_name": "Acme", "total_amount": 750.0})
    assert box["state"] == "draft"
    assert box["id"].startswith("PO-")


def test_receive_purchase_order_fully(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "total_amount": 200.0},
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))
    out = asyncio.run(skill.execute(runtime, "receive_purchase_order", {"po_id": po_id}))
    assert out["status"] == "ok" and out["state"] == "fully_received"


def test_receive_purchase_order_partial_then_full(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "total_amount": 200.0},
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))
    p = asyncio.run(skill.execute(runtime, "receive_purchase_order", {"po_id": po_id, "partial": True}))
    assert p["state"] == "partially_received"
    f = asyncio.run(skill.execute(runtime, "receive_purchase_order", {"po_id": po_id}))
    assert f["state"] == "fully_received"


def test_amend_draft_purchase_order(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "total_amount": 100.0},
    ))
    po_id = created["po_id"]
    out = asyncio.run(skill.execute(
        runtime, "amend_purchase_order",
        {"po_id": po_id, "fields": {"vendor_name": "Acme Corp", "total_amount": 250.0}},
    ))
    assert out["status"] == "amended"
    po = runtime.db.get_purchase_order(po_id)
    assert po["vendor_name"] == "Acme Corp" and po["total_amount"] == 250.0


def test_amend_blocked_after_submit(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "total_amount": 100.0},
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    out = asyncio.run(skill.execute(
        runtime, "amend_purchase_order", {"po_id": po_id, "fields": {"total_amount": 1.0}},
    ))
    assert out["status"] == "blocked"
    assert "po_not_amendable" in out["policy_precheck"]["reason_codes"]


def test_receive_line_reconciliation(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Acme", "total_amount": 25.0, "line_items": [
            {"description": "A", "quantity": 2, "unit_price": 10.0},
            {"description": "B", "quantity": 1, "unit_price": 5.0},
        ]},
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))
    # receive only line 0 fully -> partially_received
    p = asyncio.run(skill.execute(
        runtime, "receive_purchase_order",
        {"po_id": po_id, "received_lines": [{"index": 0, "quantity_received": 2}]},
    ))
    assert p["state"] == "partially_received" and p["fully_received"] is False
    # receive line 1 -> all lines fully received
    f = asyncio.run(skill.execute(
        runtime, "receive_purchase_order",
        {"po_id": po_id, "received_lines": [{"index": 1, "quantity_received": 1}]},
    ))
    assert f["state"] == "fully_received" and f["fully_received"] is True


def test_box_summary_po_extractor(runtime):
    from solden.core.box_summary import build_box_summary
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "Globex", "total_amount": 4200.0, "currency": "USD", "po_number": "PO-SUM-1"},
    ))
    summary = build_box_summary(created["po_id"], db=runtime.db, box_type="purchase_order")
    assert summary.current_stage == "draft"
    assert summary.key_fields["vendor_name"] == "Globex"
    assert summary.key_fields["amount"] == 4200.0
    assert summary.key_fields["po_number"] == "PO-SUM-1"


def test_issue_purchase_order_requires_approved(runtime):
    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "total_amount": 500.0},
    ))
    po_id = created["po_id"]
    # draft PO cannot be issued
    out = asyncio.run(skill.execute(runtime, "issue_purchase_order", {"po_id": po_id}))
    assert out["status"] == "blocked"
    assert "po_not_approved" in out["policy_precheck"]["reason_codes"]


def test_issue_purchase_order_writes_to_erp(runtime, monkeypatch):
    from types import SimpleNamespace
    from solden.integrations import erp_po_write

    skill = ProcurementFinanceSkill()
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order", {"vendor_name": "Acme", "vendor_id": "V1", "total_amount": 200.0},
    ))
    po_id = created["po_id"]
    asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))
    asyncio.run(skill.execute(runtime, "approve_purchase_order", {"po_id": po_id}))

    # mock the ERP write
    monkeypatch.setattr(erp_po_write, "is_procurement_erp_write_enabled", lambda: True)
    monkeypatch.setattr(
        "solden.integrations.erp_router.get_erp_connection",
        lambda org: SimpleNamespace(type="quickbooks", access_token="tok", realm_id="R", tenant_id=None, base_url=None),
    )

    class _Resp:
        status_code = 200

        def json(self):
            return {"PurchaseOrder": {"Id": "QB-PO-issue"}}

    class _Client:
        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(erp_po_write, "get_http_client", lambda: _Client())

    out = asyncio.run(skill.execute(runtime, "issue_purchase_order", {"po_id": po_id}))
    assert out["status"] == "issued" and out["erp_po_id"] == "QB-PO-issue"
    assert runtime.db.get_purchase_order(po_id)["erp_po_id"] == "QB-PO-issue"


def test_skill_blocks_cross_tenant_po_access(runtime):
    """A procurement intent carrying another tenant's po_id must not read or act
    on it. get_purchase_order is id-keyed (box_registry contract); the skill
    org-checks via _fetch_po, so org B sees org A's PO as not_found."""
    skill = ProcurementFinanceSkill()
    # Org A creates a PO.
    created = asyncio.run(skill.execute(
        runtime, "create_purchase_order",
        {"vendor_name": "AcmeSecret", "total_amount": 500.0},
    ))
    po_id = created["po_id"]

    # Org B runtime targets org A's po_id.
    db = db_module.get_db()
    db.ensure_organization("orgProcSkill-B", organization_name="B")
    runtime_b = FinanceAgentRuntime(
        organization_id="orgProcSkill-B", actor_id="b@test", actor_email="b@test", db=db,
    )
    out = asyncio.run(skill.execute(runtime_b, "submit_purchase_order", {"po_id": po_id}))
    assert out["status"] == "blocked"
    assert "purchase_order_not_found" in out["policy_precheck"]["reason_codes"]
    # Org A's PO is untouched (still draft).
    assert asyncio.run(skill.execute(runtime, "submit_purchase_order", {"po_id": po_id}))["state"] == "pending_approval"
