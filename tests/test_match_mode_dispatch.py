"""Tests for the match-mode dispatcher in CoordinationEngine._handle_match.

Three modes × scenarios:
  * three_way_required — PO+GR matches; PO no GR blocks; no PO blocks.
  * two_way_fallback   — PO+GR matches; PO no GR matches as 2-way;
                         PO no GR with price variance blocks; no PO
                         falls through (no_po, no block).
  * policy_only        — matching skipped entirely regardless of inputs.

The handler is exercised directly with a synthetic ctx so the test
focuses on dispatch + persistence, not full plan execution. The
exception flow is monkeypatched to a no-op so missing Gmail / LLM
credentials in the test env don't muddy the assertions.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from solden.core import database as db_module  # noqa: E402
from solden.core.coordination_engine import CoordinationEngine  # noqa: E402
from solden.core.plan import Action, Plan  # noqa: E402
from solden.services.policy_service import PolicyService  # noqa: E402
from solden.services.purchase_orders import (  # noqa: E402
    _po_to_store_dict,
    get_purchase_order_service,
)


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def db():
    inst = db_module.get_db()
    inst.initialize()
    inst.ensure_organization("orgM", organization_name="Mode Test Co")
    return inst


def _set_mode(org: str, mode: str) -> None:
    PolicyService(org).set_policy(
        "match_mode",
        content={"mode": mode},
        actor="test:set_mode",
        description=f"Test set mode={mode}",
    )


def _make_engine(db, org: str = "orgM", monkeypatch=None) -> CoordinationEngine:
    """Build a CoordinationEngine and stub out the exception flow.

    The exception flow runs four sub-actions through the full
    pre-write/execute/post-write pipeline. None of those are under
    test here (they touch Gmail + LLM + Slack), so we replace it
    with a recording no-op. Tests that need to assert "exception
    flow ran" check ``engine._exception_calls``.
    """
    engine = CoordinationEngine(db=db, organization_id=org)
    engine._exception_calls = []  # type: ignore[attr-defined]

    async def _fake_exception(plan, ctx, match_result):
        engine._exception_calls.append(  # type: ignore[attr-defined]
            {"plan": plan, "match_result": match_result}
        )

    if monkeypatch is not None:
        monkeypatch.setattr(engine, "_run_exception_flow", _fake_exception)
    else:
        engine._run_exception_flow = _fake_exception  # type: ignore[method-assign]
    return engine


def _make_po(org: str, total: float = 1000.0, currency: str = "USD"):
    """Create + approve a PO with a single line item."""
    svc = get_purchase_order_service(org)
    po = svc.create_po(
        vendor_id="VendM",
        vendor_name="VendM",
        requested_by="ops@" + org,
        line_items=[{
            "item_number": "SKU-MM",
            "description": "Widget",
            "quantity": 10,
            "unit_price": total / 10.0,
        }],
        currency=currency,
        tax_amount=0.0,
        subtotal=0.0,
    )
    po.calculate_totals()
    svc._db.save_purchase_order(_po_to_store_dict(po))
    svc.approve_po(po.po_id, approved_by="ops@" + org)
    return po


def _create_gr(org: str, po, qty: int = 10):
    svc = get_purchase_order_service(org)
    line = po.line_items[0]
    return svc.create_goods_receipt(
        po_id=po.po_id,
        received_by="warehouse@" + org,
        line_items=[{
            "po_line_id": line.line_id,
            "item_number": line.item_number,
            "description": line.description,
            "quantity_received": qty,
        }],
    )


def _make_box(db, org: str, item_id: str, *, amount: float, po_number: str):
    item = db.create_ap_item({
        "id": item_id,
        "organization_id": org,
        "vendor_name": "VendM",
        "amount": amount,
        "currency": "USD",
        "invoice_number": f"INV-{item_id}",
        "po_number": po_number,
        "state": "received",
    })
    return db.get_ap_item(item["id"])


def _run_handler(engine, *, box_id: str, vendor: str, amount: float,
                 po_number: str, po_present: bool):
    """Drive _handle_match with a synthetic ctx + plan."""
    plan = Plan(event_type="email_received", actions=[], box_id=box_id)
    engine._ctx = {
        "box_id": box_id,
        "extracted_fields": {
            "vendor_name": vendor,
            "amount": amount,
            "po_reference": po_number,
            "currency": "USD",
        },
        "po_result": {"po_number": po_number} if po_present else None,
    }
    action = Action("run_three_way_match", "DET", {}, "test")
    return asyncio.run(engine._handle_match(action, plan))


# ─── three_way_required ─────────────────────────────────────────────


class TestThreeWayRequired:
    def test_po_and_gr_present_matches(self, db, monkeypatch):
        _set_mode("orgM", "three_way_required")
        po = _make_po("orgM", total=1000.0)
        _create_gr("orgM", po)
        box = _make_box(
            db, "orgM", "MM-tw-1", amount=1000.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1000.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_passed"] is True
        assert str(result["match_status"]).upper() in ("MATCHED", "MATCH")
        assert engine._exception_calls == []

    def test_po_present_gr_missing_blocks(self, db, monkeypatch):
        _set_mode("orgM", "three_way_required")
        po = _make_po("orgM", total=1000.0)
        # No GR created.
        box = _make_box(
            db, "orgM", "MM-tw-2", amount=1000.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1000.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_passed"] is False
        assert result.get("_stop_plan") is True
        assert len(engine._exception_calls) == 1

    def test_no_po_blocks(self, db, monkeypatch):
        _set_mode("orgM", "three_way_required")
        box = _make_box(
            db, "orgM", "MM-tw-3", amount=500.0, po_number="",
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=500.0,
            po_number="", po_present=False,
        )

        assert result["match_passed"] is False
        assert result.get("_stop_plan") is True
        assert result["match_status"] == "no_po"
        assert len(engine._exception_calls) == 1


# ─── two_way_fallback (default) ─────────────────────────────────────


class TestTwoWayFallback:
    def test_po_and_gr_present_matches(self, db, monkeypatch):
        _set_mode("orgM", "two_way_fallback")
        po = _make_po("orgM", total=1000.0)
        _create_gr("orgM", po)
        box = _make_box(
            db, "orgM", "MM-2w-1", amount=1000.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1000.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_passed"] is True
        assert str(result["match_status"]).upper() in ("MATCHED", "MATCH")

    def test_po_present_gr_missing_falls_back(self, db, monkeypatch):
        """3-way's only blocker is NO_GR → promotes to 2-way match."""
        _set_mode("orgM", "two_way_fallback")
        po = _make_po("orgM", total=1000.0)
        # No GR.
        box = _make_box(
            db, "orgM", "MM-2w-2", amount=1000.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1000.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_passed"] is True
        assert result["match_status"] == "MATCHED_TWO_WAY"
        assert engine._exception_calls == []

    def test_po_present_gr_missing_with_price_variance_blocks(self, db, monkeypatch):
        """When 3-way fails for price too, 2-way fallback can't save it."""
        _set_mode("orgM", "two_way_fallback")
        po = _make_po("orgM", total=1000.0)
        # No GR + invoice 50% over PO.
        box = _make_box(
            db, "orgM", "MM-2w-3", amount=1500.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1500.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_passed"] is False
        assert result.get("_stop_plan") is True
        assert len(engine._exception_calls) == 1

    def test_no_po_falls_through(self, db, monkeypatch):
        """No PO from ERP lookup → no_po, but does not block. AP
        item routes via approval thresholds downstream."""
        _set_mode("orgM", "two_way_fallback")
        box = _make_box(
            db, "orgM", "MM-2w-4", amount=500.0, po_number="",
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=500.0,
            po_number="", po_present=False,
        )

        assert result["match_status"] == "no_po"
        assert "match_passed" not in result or result.get("match_passed") is None
        assert engine._exception_calls == []


# ─── policy_only ────────────────────────────────────────────────────


class TestPolicyOnly:
    def test_with_po_and_gr_skips_match(self, db, monkeypatch):
        _set_mode("orgM", "policy_only")
        po = _make_po("orgM", total=1000.0)
        _create_gr("orgM", po)
        box = _make_box(
            db, "orgM", "MM-po-1", amount=1000.0, po_number=po.po_number,
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=1000.0,
            po_number=po.po_number, po_present=True,
        )

        assert result["match_status"] == "skipped_by_policy"
        assert result["match_passed"] is True
        # The Box's match_status field should reflect the skip.
        item = db.get_ap_item(box["id"])
        assert item["match_status"] == "skipped_by_policy"

    def test_no_po_skips_match(self, db, monkeypatch):
        _set_mode("orgM", "policy_only")
        box = _make_box(
            db, "orgM", "MM-po-2", amount=500.0, po_number="",
        )
        engine = _make_engine(db, monkeypatch=monkeypatch)

        result = _run_handler(
            engine,
            box_id=box["id"], vendor="VendM", amount=500.0,
            po_number="", po_present=False,
        )

        assert result["match_status"] == "skipped_by_policy"
        assert result["match_passed"] is True


# ─── PolicyService surface for match_mode ───────────────────────────


class TestPolicyServiceMatchMode:
    def test_default_mode_is_two_way_fallback(self, db):
        # Use a fresh org so the lazy migration writes v1 from default.
        db.ensure_organization("orgFresh", organization_name="Fresh Co")
        version = PolicyService("orgFresh").get_active("match_mode")
        assert version.content == {"mode": "two_way_fallback"}

    def test_set_policy_creates_new_version(self, db):
        db.ensure_organization("orgVer", organization_name="Vers Co")
        svc = PolicyService("orgVer")
        v1 = svc.get_active("match_mode")
        v2 = svc.set_policy(
            "match_mode",
            content={"mode": "three_way_required"},
            actor="test:upgrade",
            description="Tighten to 3-way",
        )
        assert v2.version_number > v1.version_number
        assert v2.content == {"mode": "three_way_required"}

        active = svc.get_active("match_mode")
        assert active.id == v2.id
