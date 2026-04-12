"""Tests for action idempotency — Agent Design Specification §12.3."""
from __future__ import annotations

import asyncio

import pytest

from clearledgr.core.database import get_db
from clearledgr.core.execution_engine import ExecutionEngine
from clearledgr.core.plan import Action, Plan


@pytest.fixture
def engine():
    db = get_db()
    return ExecutionEngine(db=db, organization_id="idem-test-org")


class TestReadOnlyActionIdempotency:
    """Read-only actions should be safely repeatable."""

    def test_read_email_idempotent_without_content(self, engine):
        plan = Plan(event_type="test", actions=[], organization_id="test")
        action = Action("read_email", "DET", {}, "Read")
        r1 = asyncio.run(engine._handle_read_email(action, plan))
        r2 = asyncio.run(engine._handle_read_email(action, plan))
        assert r1 == r2

    def test_lookup_po_idempotent(self, engine):
        plan = Plan(event_type="test", actions=[], organization_id="test")
        engine._ctx["extracted_fields"] = {"po_reference": "PO-TEST-001"}
        action = Action("lookup_po", "DET", {}, "Lookup")
        r1 = asyncio.run(engine._handle_lookup_po(action, plan))
        r2 = asyncio.run(engine._handle_lookup_po(action, plan))
        assert r1.get("po_found") == r2.get("po_found")

    def test_check_domain_match_idempotent(self, engine):
        plan = Plan(event_type="test", actions=[], organization_id="test")
        engine._ctx["sender"] = "billing@test.com"
        engine._ctx["extracted_fields"] = {"vendor_name": "Test Vendor"}
        action = Action("check_domain_match", "DET", {}, "Check")
        r1 = asyncio.run(engine._handle_domain_match(action, plan))
        r2 = asyncio.run(engine._handle_domain_match(action, plan))
        assert r1.get("domain_status") == r2.get("domain_status")


class TestStateWriteIdempotency:

    def test_set_waiting_condition_idempotent(self, engine):
        plan = Plan(event_type="test", actions=[], organization_id="test")
        action = Action("set_waiting_condition", "DET",
                        {"type": "approval_response", "timeout_hours": 4},
                        "Set waiting")
        r1 = asyncio.run(engine._handle_set_waiting(action, plan))
        r2 = asyncio.run(engine._handle_set_waiting(action, plan))
        assert r1.get("waiting_condition", {}).get("type") == r2.get("waiting_condition", {}).get("type")

    def test_clear_waiting_condition_idempotent(self, engine):
        plan = Plan(event_type="test", actions=[], box_id="idem-box-001", organization_id="test")
        action = Action("clear_waiting_condition", "DET", {}, "Clear")
        r1 = asyncio.run(engine._handle_clear_waiting(action, plan))
        r2 = asyncio.run(engine._handle_clear_waiting(action, plan))
        assert r1 == r2


class TestPrePostValidateIdempotency:
    """§12.3: post_bill uses pre_post_validate to catch existing bills."""

    def test_pre_post_validate_catches_already_posted(self):
        from clearledgr.integrations.erp_router import pre_post_validate
        from unittest.mock import MagicMock

        mock_db = MagicMock()
        mock_db.get_ap_item.return_value = {
            "id": "test-item",
            "erp_reference": "BILL-EXISTING-001",
            "invoice_number": "INV-001",
            "vendor_name": "Test Vendor",
        }
        mock_db.list_ap_items.return_value = []

        result = pre_post_validate("test-item", "test-org", db=mock_db)
        assert not result["valid"]
        failures = result["failures"]
        assert any(f["check"] == "already_posted" for f in failures)


class TestActionReturnsDict:
    """Every action handler must return a dict."""

    def test_all_handlers_return_dict(self, engine):
        plan = Plan(event_type="test", actions=[], organization_id="test")
        for name, handler in engine._handlers.items():
            action = Action(name, "DET", {}, f"Test {name}")
            try:
                result = asyncio.run(handler(action, plan))
                assert isinstance(result, dict), f"{name} returned {type(result)}, not dict"
            except Exception as exc:
                pytest.fail(f"{name} raised {type(exc).__name__}: {exc}")
