"""Tests for ERP follow-on operations: finance effect review blockers,
non-invoice ERP follow-on dispatch, connector strategy route plans,
and browser macro command generation.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core import database as db_module
from clearledgr.core.ap_states import APState
from clearledgr.services.ap_item_service import (
    _execute_non_invoice_erp_follow_on,
    _normalize_document_type_token,
    _normalize_non_invoice_outcome,
    _parse_json,
    _safe_float,
)
from clearledgr.services.browser_agent import BrowserAgentService, SUPPORTED_MACROS
from clearledgr.services.erp_connector_strategy import (
    ERPConnectorStrategy,
    ConnectorCapability,
)
from clearledgr.services.erp_follow_on_result import (
    _ERP_FOLLOW_ON_APPLIED_STATUSES,
    _ERP_FOLLOW_ON_PENDING_STATUSES,
    _finance_effect_review_blockers,
    _money_amount,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "erp-follow-on.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._DB_INSTANCE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_ap_item(
    db,
    *,
    item_id: str,
    thread_id: str = "thread-1",
    state: str = "posted_to_erp",
    document_type: str = "invoice",
    amount: float = 1000.0,
    currency: str = "USD",
    erp_reference: str = "",
    metadata: Dict[str, Any] | None = None,
) -> dict:
    return db.create_ap_item(
        {
            "id": item_id,
            "invoice_key": f"inv-{item_id}",
            "thread_id": thread_id,
            "message_id": f"msg-{thread_id}",
            "subject": f"Invoice {item_id}",
            "sender": "billing@example.com",
            "vendor_name": "Acme",
            "amount": amount,
            "currency": currency,
            "invoice_number": f"INV-{item_id}",
            "state": state,
            "confidence": 0.99,
            "organization_id": "default",
            "erp_reference": erp_reference,
            "document_type": document_type,
            "metadata": metadata or {},
        }
    )


# ===================================================================
# Category 1: _finance_effect_review_blockers() unit tests
# ===================================================================


class TestFinanceEffectReviewBlockers:
    """Pure-function tests for blocker conditions on linked finance effects."""

    def _base_kwargs(self, **overrides) -> dict:
        """Default kwargs representing a clean invoice with no linked effects."""
        defaults = {
            "related_document_type": "invoice",
            "related_state": APState.NEEDS_APPROVAL.value,
            "original_amount": 1000.0,
            "applied_credit_total": 0.0,
            "gross_cash_out_total": 0.0,
            "refund_total": 0.0,
            "over_credit_amount": 0.0,
            "overpayment_amount": 0.0,
            "credit_erp_status": "not_applicable",
            "cash_erp_status": "not_applicable",
        }
        defaults.update(overrides)
        return defaults

    def test_no_blockers_when_no_linked_finance_effects(self):
        blockers = _finance_effect_review_blockers(**self._base_kwargs())
        assert blockers == []

    def test_block_linked_finance_target_amount_missing(self):
        """original_amount <= 0 with credits present triggers amount-missing blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                original_amount=0.0,
                applied_credit_total=200.0,
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_finance_target_amount_missing" in codes

    def test_block_linked_credit_application_pending(self):
        """Credit with ERP status in pending statuses triggers pending blocker."""
        for pending_status in ("pending", "queued", "requested", "pending_browser_fallback", "pending_target_post"):
            blockers = _finance_effect_review_blockers(
                **self._base_kwargs(
                    applied_credit_total=100.0,
                    credit_erp_status=pending_status,
                )
            )
            codes = [b["code"] for b in blockers]
            assert "linked_credit_application_pending" in codes, (
                f"Expected pending blocker for status={pending_status}"
            )

    def test_block_linked_credit_adjustment_present(self):
        """Credit with ERP status NOT in applied or pending triggers adjustment blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                applied_credit_total=100.0,
                credit_erp_status="not_requested",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_credit_adjustment_present" in codes

    def test_no_block_credit_in_applied_statuses(self):
        """Credit with ERP status in applied statuses should not trigger credit blockers."""
        for applied_status in ("applied", "success", "completed", "already_applied"):
            blockers = _finance_effect_review_blockers(
                **self._base_kwargs(
                    applied_credit_total=100.0,
                    credit_erp_status=applied_status,
                )
            )
            codes = [b["code"] for b in blockers]
            assert "linked_credit_application_pending" not in codes
            assert "linked_credit_adjustment_present" not in codes

    def test_block_linked_settlement_application_pending(self):
        """Settlement with ERP status in pending statuses triggers pending blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                gross_cash_out_total=500.0,
                cash_erp_status="pending",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_settlement_application_pending" in codes

    def test_block_linked_cash_application_present(self):
        """Settlement with ERP status NOT in applied or pending triggers cash-application blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                gross_cash_out_total=500.0,
                cash_erp_status="not_requested",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_cash_application_present" in codes

    def test_block_linked_over_credit(self):
        """Credits exceeding invoice amount triggers over-credit blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                applied_credit_total=1500.0,
                over_credit_amount=500.0,
                credit_erp_status="applied",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_over_credit" in codes

    def test_block_linked_overpayment(self):
        """Payments exceeding remaining payable triggers overpayment blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                gross_cash_out_total=1200.0,
                overpayment_amount=200.0,
                cash_erp_status="applied",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_overpayment" in codes

    def test_both_credit_and_settlement_pending(self):
        """Both credit AND settlement pending produces both blockers simultaneously."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                applied_credit_total=200.0,
                credit_erp_status="pending",
                gross_cash_out_total=300.0,
                cash_erp_status="queued",
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_credit_application_pending" in codes
        assert "linked_settlement_application_pending" in codes
        assert len(codes) >= 2

    def test_no_credit_blocker_for_posted_invoice(self):
        """posted_to_erp or closed invoices are not 'active' so credit blockers do not fire."""
        for terminal_state in (APState.POSTED_TO_ERP.value, APState.CLOSED.value, APState.REJECTED.value):
            blockers = _finance_effect_review_blockers(
                **self._base_kwargs(
                    related_state=terminal_state,
                    applied_credit_total=100.0,
                    credit_erp_status="not_requested",
                )
            )
            codes = [b["code"] for b in blockers]
            # The credit-adjustment blocker only fires for active invoices
            assert "linked_credit_adjustment_present" not in codes
            assert "linked_credit_application_pending" not in codes

    def test_non_invoice_target_produces_target_not_invoice_blocker(self):
        """Linked finance effects pointing at a non-invoice record produce the target_not_invoice blocker."""
        blockers = _finance_effect_review_blockers(
            **self._base_kwargs(
                related_document_type="credit_note",
                applied_credit_total=100.0,
            )
        )
        codes = [b["code"] for b in blockers]
        assert "linked_finance_target_not_invoice" in codes


# ===================================================================
# Category 2: _execute_non_invoice_erp_follow_on() tests
# ===================================================================


class TestExecuteNonInvoiceERPFollowOn:
    """Tests for the async dispatch function that routes credit note / settlement
    ERP follow-on operations."""

    def test_currency_mismatch_returns_error(self, db):
        source_item = {
            "id": "SRC-1",
            "currency": "USD",
            "amount": 50.0,
            "invoice_number": "CN-001",
            "subject": "Credit note",
            "message_id": "msg-src-1",
            "metadata": "{}",
        }
        related_item = {
            "id": "REL-1",
            "currency": "EUR",
            "state": APState.POSTED_TO_ERP.value,
            "erp_reference": "ERP-REF-001",
            "invoice_number": "INV-001",
            "metadata": "{}",
        }

        async def _run():
            return await _execute_non_invoice_erp_follow_on(
                db,
                source_item=source_item,
                related_item=related_item,
                document_type="credit_note",
                outcome="apply_to_invoice",
                actor_id="test-user",
                organization_id="default",
            )
        result = asyncio.run(_run())
        assert result is not None
        assert result["status"] == "error"
        assert result["reason"] == "currency_mismatch"
        assert result["source_currency"] == "USD"
        assert result["target_currency"] == "EUR"

    def test_same_currency_proceeds(self, db):
        """When currencies match and target is posted, the function dispatches to api_first."""
        source = _create_ap_item(db, item_id="SRC-2", state="received", document_type="credit_note", amount=50.0)
        related = _create_ap_item(db, item_id="REL-2", state="posted_to_erp", erp_reference="ERP-REF-002")

        mock_api = AsyncMock(return_value={"status": "success", "erp_reference": "CR-123"})
        with patch("clearledgr.services.ap_item_service.apply_credit_note_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="credit_note",
                    outcome="apply_to_invoice",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())
        assert result is not None
        assert mock_api.called
        # Result goes through _apply_erp_follow_on_result which returns source_item, related_item, follow_on
        assert "follow_on" in result
        assert result["follow_on"]["action_type"] == "apply_credit_note"

    def test_unrecognized_doc_type_returns_none(self, db):
        """Unrecognized document_type + outcome combination returns None (logged, not dispatched)."""
        source_item = {
            "id": "SRC-3",
            "currency": "USD",
            "amount": 100.0,
            "invoice_number": "STMT-001",
            "subject": "Statement",
            "message_id": "msg-src-3",
            "metadata": "{}",
        }
        related_item = {
            "id": "REL-3",
            "currency": "USD",
            "state": APState.POSTED_TO_ERP.value,
            "erp_reference": "ERP-REF-003",
            "invoice_number": "INV-003",
            "metadata": "{}",
        }

        async def _run():
            return await _execute_non_invoice_erp_follow_on(
                db,
                source_item=source_item,
                related_item=related_item,
                document_type="statement",
                outcome="send_to_reconciliation",
                actor_id="test-user",
                organization_id="default",
            )
        result = asyncio.run(_run())
        assert result is None

    def test_target_not_posted_returns_skipped(self, db):
        """When target invoice is not yet posted_to_erp, returns skipped/pending_target_post."""
        source = _create_ap_item(db, item_id="SRC-4", state="received", document_type="credit_note", amount=50.0)
        related = _create_ap_item(db, item_id="REL-4", state="needs_approval", erp_reference="")

        async def _run():
            return await _execute_non_invoice_erp_follow_on(
                db,
                source_item=source,
                related_item=related,
                document_type="credit_note",
                outcome="apply_to_invoice",
                actor_id="test-user",
                organization_id="default",
            )
        result = asyncio.run(_run())
        assert result is not None
        assert "follow_on" in result
        follow_on = result["follow_on"]
        assert follow_on["status"] in ("pending_target_post", "skipped")

    def test_credit_note_posted_target_dispatches_api_first(self, db):
        """credit_note + apply_to_invoice + posted target -> dispatches apply_credit_note_api_first."""
        source = _create_ap_item(
            db, item_id="SRC-5", state="received", document_type="credit_note",
            amount=75.0, currency="USD",
        )
        related = _create_ap_item(
            db, item_id="REL-5", state="posted_to_erp",
            erp_reference="ERP-REF-005", currency="USD",
        )

        mock_api = AsyncMock(return_value={"status": "success", "erp_reference": "CR-005"})
        with patch("clearledgr.services.ap_item_service.apply_credit_note_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="credit_note",
                    outcome="apply_to_invoice",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())

        mock_api.assert_called_once()
        call_kwargs = mock_api.call_args.kwargs
        assert call_kwargs["organization_id"] == "default"
        assert call_kwargs["target_erp_reference"] == "ERP-REF-005"
        assert call_kwargs["amount"] == 75.0
        assert call_kwargs["currency"] == "USD"

        assert result is not None
        assert result["follow_on"]["action_type"] == "apply_credit_note"

    def test_refund_posted_target_dispatches_settlement_api_first(self, db):
        """refund + link_to_payment + posted target -> dispatches apply_settlement_api_first."""
        source = _create_ap_item(
            db, item_id="SRC-6", state="received", document_type="refund",
            amount=200.0, currency="USD",
        )
        related = _create_ap_item(
            db, item_id="REL-6", state="posted_to_erp",
            erp_reference="ERP-REF-006", currency="USD",
        )

        mock_api = AsyncMock(return_value={"status": "success", "erp_reference": "SET-006"})
        with patch("clearledgr.services.ap_item_service.apply_settlement_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="refund",
                    outcome="link_to_payment",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())

        mock_api.assert_called_once()
        call_kwargs = mock_api.call_args.kwargs
        assert call_kwargs["source_document_type"] == "refund"
        assert call_kwargs["amount"] == 200.0

        assert result is not None
        assert result["follow_on"]["action_type"] == "apply_settlement"

    def test_api_first_exception_returns_internal_error(self, db):
        """When apply_credit_note_api_first raises, the function catches it and returns internal_error."""
        source = _create_ap_item(
            db, item_id="SRC-7", state="received", document_type="credit_note",
            amount=100.0, currency="USD",
        )
        related = _create_ap_item(
            db, item_id="REL-7", state="posted_to_erp",
            erp_reference="ERP-REF-007", currency="USD",
        )

        mock_api = AsyncMock(side_effect=RuntimeError("ERP connection timeout"))
        with patch("clearledgr.services.ap_item_service.apply_credit_note_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="credit_note",
                    outcome="apply_to_invoice",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())

        # Exception is caught, not propagated
        assert result is not None
        assert result["follow_on"]["status"] == "failed"

    def test_settlement_api_first_exception_returns_internal_error(self, db):
        """When apply_settlement_api_first raises, the function catches it and returns internal_error."""
        source = _create_ap_item(
            db, item_id="SRC-8", state="received", document_type="payment",
            amount=300.0, currency="USD",
        )
        related = _create_ap_item(
            db, item_id="REL-8", state="posted_to_erp",
            erp_reference="ERP-REF-008", currency="USD",
        )

        mock_api = AsyncMock(side_effect=Exception("unexpected failure"))
        with patch("clearledgr.services.ap_item_service.apply_settlement_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="payment",
                    outcome="link_to_payment",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())

        assert result is not None
        assert result["follow_on"]["status"] == "failed"

    def test_receipt_dispatches_settlement(self, db):
        """receipt + link_to_payment also dispatches apply_settlement_api_first (not just refund)."""
        source = _create_ap_item(
            db, item_id="SRC-9", state="received", document_type="receipt",
            amount=150.0, currency="USD",
        )
        related = _create_ap_item(
            db, item_id="REL-9", state="posted_to_erp",
            erp_reference="ERP-REF-009", currency="USD",
        )

        mock_api = AsyncMock(return_value={"status": "success"})
        with patch("clearledgr.services.ap_item_service.apply_settlement_api_first", mock_api):
            async def _run():
                return await _execute_non_invoice_erp_follow_on(
                    db,
                    source_item=source,
                    related_item=related,
                    document_type="receipt",
                    outcome="link_to_payment",
                    actor_id="test-user",
                    organization_id="default",
                )
            result = asyncio.run(_run())

        mock_api.assert_called_once()
        assert result is not None
        assert result["follow_on"]["action_type"] == "apply_settlement"


# ===================================================================
# Category 3: ERPConnectorStrategy.build_route_plan() tests
# ===================================================================


class TestERPConnectorStrategyBuildRoutePlan:
    """Tests for the connector strategy route plan builder."""

    def test_apply_credit_returns_api_mode_for_quickbooks(self):
        """QuickBooks credit applications should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="quickbooks",
            connection_present=True,
            action="apply_credit",
        )
        assert plan["action"] == "apply_credit"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"
        assert plan["erp_type"] == "quickbooks"

    def test_apply_settlement_returns_api_mode_for_quickbooks(self):
        """QuickBooks settlements should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="quickbooks",
            connection_present=True,
            action="apply_settlement",
        )
        assert plan["action"] == "apply_settlement"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"

    def test_apply_credit_returns_api_mode_for_xero(self):
        """Xero credit applications should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="xero",
            connection_present=True,
            action="apply_credit",
        )
        assert plan["action"] == "apply_credit"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"
        assert plan["erp_type"] == "xero"
        assert plan["connection_present"] is True

    def test_apply_settlement_returns_api_mode_for_xero(self):
        """Xero settlements should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="xero",
            connection_present=True,
            action="apply_settlement",
        )
        assert plan["action"] == "apply_settlement"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"

    def test_apply_credit_returns_api_mode_for_netsuite(self):
        """NetSuite credit applications should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="netsuite",
            connection_present=True,
            action="apply_credit",
        )
        assert plan["action"] == "apply_credit"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"
        assert plan["erp_type"] == "netsuite"

    def test_apply_settlement_returns_api_mode_for_netsuite(self):
        """NetSuite settlements should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="netsuite",
            connection_present=True,
            action="apply_settlement",
        )
        assert plan["action"] == "apply_settlement"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"

    def test_apply_credit_returns_api_mode_for_sap(self):
        """SAP credit applications should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="sap",
            connection_present=True,
            action="apply_credit",
        )
        assert plan["action"] == "apply_credit"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"
        assert plan["erp_type"] == "sap"

    def test_apply_settlement_returns_api_mode_for_sap(self):
        """SAP settlements should prefer the native API path."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="sap",
            connection_present=True,
            action="apply_settlement",
        )
        assert plan["action"] == "apply_settlement"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"

    def test_post_bill_with_connection_returns_api_mode(self):
        """action='post_bill' with connection_present=True returns api mode for QBO."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="quickbooks",
            connection_present=True,
            action="post_bill",
        )
        assert plan["action"] == "post_bill"
        assert plan["api_supported"] is True
        assert plan["primary_mode"] == "api"

    def test_unknown_action_falls_back_gracefully(self):
        """Unknown action defaults to api_supported=False."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="quickbooks",
            connection_present=True,
            action="some_unknown_action",
        )
        assert plan["action"] == "some_unknown_action"
        assert plan["api_supported"] is False
        assert plan["primary_mode"] == "browser_fallback"

    def test_no_connection_falls_back_to_unconfigured(self):
        """connection_present=False resolves to the 'unconfigured' capability."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="quickbooks",
            connection_present=False,
            action="post_bill",
        )
        assert plan["erp_type"] == "unconfigured"
        assert plan["api_supported"] is False
        assert plan["primary_mode"] == "browser_fallback"

    def test_unknown_erp_type_resolves_to_unknown_capability(self):
        """Unrecognized erp_type falls to the 'unknown' capability (no browser fallback)."""
        strategy = ERPConnectorStrategy()
        plan = strategy.build_route_plan(
            erp_type="oracle_fusion",
            connection_present=True,
            action="post_bill",
        )
        assert plan["erp_type"] == "unknown"
        assert plan["api_supported"] is False
        assert plan["fallback_enabled"] is False
        assert plan["primary_mode"] == "manual_review"

    def test_all_configured_erps_support_credit_application_api(self):
        """All first-party connectors now expose a native credit-application path."""
        strategy = ERPConnectorStrategy()
        for erp_type in ("quickbooks", "xero", "netsuite", "sap"):
            plan = strategy.build_route_plan(
                erp_type=erp_type,
                connection_present=True,
                action="apply_credit",
            )
            assert plan["api_supported"] is True, f"Expected api_supported=True for {erp_type}"
            assert plan["primary_mode"] == "api"

    def test_all_configured_erps_support_settlement_application_api(self):
        """All first-party connectors now expose a native settlement path."""
        strategy = ERPConnectorStrategy()
        for erp_type in ("quickbooks", "xero", "netsuite", "sap"):
            plan = strategy.build_route_plan(
                erp_type=erp_type,
                connection_present=True,
                action="apply_settlement",
            )
            assert plan["api_supported"] is True, f"Expected api_supported=True for {erp_type}"
            assert plan["primary_mode"] == "api"


# ===================================================================
# Category 4: Browser macro command generation
# ===================================================================


class TestBrowserMacroCommandGeneration:
    """Tests for the BrowserAgentService._build_macro_commands method
    for apply_credit_note_in_erp and apply_settlement_in_erp macros."""

    def _make_service(self, db, monkeypatch) -> BrowserAgentService:
        """Create a BrowserAgentService wired to the test DB."""
        monkeypatch.setenv("AP_BROWSER_AGENT_ENABLED", "true")
        service = BrowserAgentService.__new__(BrowserAgentService)
        service.db = db
        service.enabled = True
        return service

    def test_apply_credit_note_macro_generates_correct_commands(self, db, monkeypatch):
        """apply_credit_note_in_erp macro generates the expected command sequence."""
        item = _create_ap_item(db, item_id="MACRO-CN-1", thread_id="thread-macro-cn")
        service = self._make_service(db, monkeypatch)

        session = {"ap_item_id": "MACRO-CN-1", "organization_id": "default"}
        params = {
            "erp_url": "https://erp.example.com/payable/INV-001",
            "target_erp_reference": "ERP-REF-100",
            "credit_note_number": "CN-200",
            "amount": 500.0,
            "currency": "USD",
        }

        commands = service._build_macro_commands(
            session, "apply_credit_note_in_erp", params, "corr-id-1"
        )

        assert len(commands) == 7
        tool_names = [cmd["tool_name"] for cmd in commands]
        assert tool_names == [
            "open_tab",
            "type",
            "click",
            "type",
            "type",
            "click",
            "capture_evidence",
        ]

        # First command opens the ERP URL
        assert commands[0]["command_id"] == "macro_credit_open_erp"
        assert commands[0]["target"]["url"] == "https://erp.example.com/payable/INV-001"

        # Target reference search
        assert commands[1]["command_id"] == "macro_credit_target_ref"
        assert commands[1]["params"]["value"] == "ERP-REF-100"

        # Credit note number fill
        assert commands[3]["command_id"] == "macro_credit_number"
        assert commands[3]["params"]["value"] == "CN-200"

        # Amount fill
        assert commands[4]["command_id"] == "macro_credit_amount"
        assert commands[4]["params"]["value"] == "500.0"

        # Submit
        assert commands[5]["command_id"] == "macro_credit_submit"

        # Evidence capture
        assert commands[6]["command_id"] == "macro_credit_capture_result"
        assert commands[6]["tool_name"] == "capture_evidence"

        # Correlation ID is set on all commands
        for cmd in commands:
            assert cmd["correlation_id"] == "corr-id-1"

    def test_apply_settlement_macro_generates_correct_commands(self, db, monkeypatch):
        """apply_settlement_in_erp macro generates the expected command sequence."""
        item = _create_ap_item(db, item_id="MACRO-SET-1", thread_id="thread-macro-set")
        service = self._make_service(db, monkeypatch)

        session = {"ap_item_id": "MACRO-SET-1", "organization_id": "default"}
        params = {
            "erp_url": "https://erp.example.com/payable/INV-002",
            "target_erp_reference": "ERP-REF-200",
            "source_reference": "PMT-300",
            "amount": 750.0,
            "currency": "GBP",
        }

        commands = service._build_macro_commands(
            session, "apply_settlement_in_erp", params, "corr-id-2"
        )

        assert len(commands) == 7
        tool_names = [cmd["tool_name"] for cmd in commands]
        assert tool_names == [
            "open_tab",
            "type",
            "click",
            "type",
            "type",
            "click",
            "capture_evidence",
        ]

        # First command opens the ERP URL
        assert commands[0]["command_id"] == "macro_settlement_open_erp"
        assert commands[0]["target"]["url"] == "https://erp.example.com/payable/INV-002"

        # Target reference search
        assert commands[1]["command_id"] == "macro_settlement_target_ref"
        assert commands[1]["params"]["value"] == "ERP-REF-200"

        # Settlement flow open
        assert commands[2]["command_id"] == "macro_settlement_open_apply"

        # Source reference fill
        assert commands[3]["command_id"] == "macro_settlement_source_ref"
        assert commands[3]["params"]["value"] == "PMT-300"

        # Amount fill
        assert commands[4]["command_id"] == "macro_settlement_amount"
        assert commands[4]["params"]["value"] == "750.0"

        # Submit
        assert commands[5]["command_id"] == "macro_settlement_submit"
        assert "Apply Payment" in str(commands[5]["params"]["selector_candidates"])

        # Evidence capture
        assert commands[6]["command_id"] == "macro_settlement_capture_result"
        assert commands[6]["tool_name"] == "capture_evidence"

        # Correlation ID is set on all commands
        for cmd in commands:
            assert cmd["correlation_id"] == "corr-id-2"

    def test_unsupported_macro_raises_value_error(self, db, monkeypatch):
        """Unsupported macro name raises ValueError."""
        item = _create_ap_item(db, item_id="MACRO-FAIL", thread_id="thread-macro-fail")
        service = self._make_service(db, monkeypatch)
        session = {"ap_item_id": "MACRO-FAIL", "organization_id": "default"}

        with pytest.raises(ValueError, match="macro_not_supported"):
            service._build_macro_commands(session, "nonexistent_macro", {}, None)


# ===================================================================
# Category 5: Helper function unit tests
# ===================================================================


class TestHelperFunctions:
    """Tests for _normalize_document_type_token, _normalize_non_invoice_outcome,
    _parse_json, _safe_float, _money_amount."""

    def test_normalize_document_type_token_credit_memo(self):
        assert _normalize_document_type_token("credit_memo") == "credit_note"
        assert _normalize_document_type_token("Credit-Memo") == "credit_note"
        assert _normalize_document_type_token("credit memo") == "credit_note"

    def test_normalize_document_type_token_payment_confirmation(self):
        assert _normalize_document_type_token("payment_confirmation") == "receipt"

    def test_normalize_document_type_token_bank_statement(self):
        assert _normalize_document_type_token("bank_statement") == "statement"

    def test_normalize_document_type_token_empty_defaults_to_invoice(self):
        assert _normalize_document_type_token("") == "invoice"
        assert _normalize_document_type_token(None) == "invoice"

    def test_normalize_document_type_token_passthrough(self):
        assert _normalize_document_type_token("refund") == "refund"
        assert _normalize_document_type_token("invoice") == "invoice"

    def test_normalize_non_invoice_outcome(self):
        assert _normalize_non_invoice_outcome("apply-to-invoice") == "apply_to_invoice"
        assert _normalize_non_invoice_outcome("Link To Payment") == "link_to_payment"
        assert _normalize_non_invoice_outcome(None) == ""

    def test_parse_json_dict_passthrough(self):
        assert _parse_json({"key": "val"}) == {"key": "val"}

    def test_parse_json_string(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_parse_json_invalid(self):
        assert _parse_json("not json") == {}
        assert _parse_json(None) == {}
        assert _parse_json(42) == {}

    def test_safe_float(self):
        assert _safe_float("123.45") == 123.45
        assert _safe_float(None) == 0.0
        assert _safe_float("bad") == 0.0
        assert _safe_float(42) == 42.0

    def test_money_amount(self):
        assert _money_amount(-150.555) == 150.56
        assert _money_amount(0) == 0.0
        assert _money_amount("bad") == 0.0
        assert _money_amount(100) == 100.0


# ===================================================================
# Category 6: Status set membership
# ===================================================================


class TestERPFollowOnStatusSets:
    """Validate the applied and pending status sets are correctly defined."""

    def test_applied_statuses_membership(self):
        assert "applied" in _ERP_FOLLOW_ON_APPLIED_STATUSES
        assert "success" in _ERP_FOLLOW_ON_APPLIED_STATUSES
        assert "completed" in _ERP_FOLLOW_ON_APPLIED_STATUSES
        assert "already_applied" in _ERP_FOLLOW_ON_APPLIED_STATUSES

    def test_pending_statuses_membership(self):
        assert "pending" in _ERP_FOLLOW_ON_PENDING_STATUSES
        assert "queued" in _ERP_FOLLOW_ON_PENDING_STATUSES
        assert "requested" in _ERP_FOLLOW_ON_PENDING_STATUSES
        assert "pending_browser_fallback" in _ERP_FOLLOW_ON_PENDING_STATUSES
        assert "pending_target_post" in _ERP_FOLLOW_ON_PENDING_STATUSES

    def test_applied_and_pending_are_disjoint(self):
        assert _ERP_FOLLOW_ON_APPLIED_STATUSES.isdisjoint(_ERP_FOLLOW_ON_PENDING_STATUSES)

    def test_supported_macros_include_finance_follow_ons(self):
        assert "apply_credit_note_in_erp" in SUPPORTED_MACROS
        assert "apply_settlement_in_erp" in SUPPORTED_MACROS


# ===================================================================
# Category 7: reconcile_erp_follow_on_state() tests
# ===================================================================


from clearledgr.services.erp_follow_on_reconciliation import reconcile_erp_follow_on_state


class TestERPFollowOnReconciliation:
    """Tests for the reconciliation check that detects and repairs
    split-brain state between source follow-on status and related
    item summaries."""

    def test_no_items_returns_zero(self, db):
        """Empty DB returns checked=0."""
        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 0
        assert result["mismatches"] == 0
        assert result["repaired"] == 0
        assert result["errors"] == 0

    def test_no_follow_on_items_skipped(self, db):
        """Items without non_invoice_resolution.erp_follow_on are skipped."""
        # Item with no metadata at all
        _create_ap_item(db, item_id="PLAIN-1")
        # Item with metadata but no non_invoice_resolution
        _create_ap_item(db, item_id="PLAIN-2", metadata={"some_key": "val"})
        # Item with non_invoice_resolution but no erp_follow_on
        _create_ap_item(db, item_id="PLAIN-3", metadata={
            "non_invoice_resolution": {"outcome": "mark_as_duplicate"},
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 0
        assert result["mismatches"] == 0

    def test_matching_statuses_no_repair(self, db):
        """Source status matches related status — no repair needed."""
        # Create the related item with matching credit summary
        _create_ap_item(db, item_id="REL-MATCH", state="posted_to_erp", metadata={
            "vendor_credit_summary": {
                "erp_application_status": "applied",
            },
        })
        # Create source item with follow-on pointing to the related item
        _create_ap_item(db, item_id="SRC-MATCH", state="posted_to_erp", metadata={
            "non_invoice_resolution": {
                "related_ap_item_id": "REL-MATCH",
                "erp_follow_on": {
                    "status": "applied",
                    "action_type": "apply_credit_note",
                },
            },
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 1
        assert result["mismatches"] == 0
        assert result["repaired"] == 0

    def test_credit_mismatch_repaired(self, db):
        """Source has 'applied' but related has 'pending' — repair propagates
        source status to related's vendor_credit_summary.erp_application_status."""
        _create_ap_item(db, item_id="REL-CREDIT", state="posted_to_erp", metadata={
            "vendor_credit_summary": {
                "erp_application_status": "pending",
            },
        })
        _create_ap_item(db, item_id="SRC-CREDIT", state="posted_to_erp", metadata={
            "non_invoice_resolution": {
                "related_ap_item_id": "REL-CREDIT",
                "erp_follow_on": {
                    "status": "applied",
                    "action_type": "apply_credit_note",
                    "execution_mode": "api",
                    "erp_reference": "CR-999",
                },
            },
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 1
        assert result["mismatches"] == 1
        assert result["repaired"] == 1
        assert result["errors"] == 0

        # Verify the related item's metadata was updated
        related = db.get_ap_item("REL-CREDIT")
        related_meta = json.loads(related["metadata"]) if isinstance(related["metadata"], str) else related["metadata"]
        credit_summary = related_meta["vendor_credit_summary"]
        assert credit_summary["erp_application_status"] == "applied"
        assert credit_summary["erp_application_mode"] == "api"
        assert credit_summary["erp_application_reference"] == "CR-999"
        assert "erp_reconciled_at" in credit_summary

    def test_settlement_mismatch_repaired(self, db):
        """Source has 'applied' but related has 'pending' — repair propagates
        source status to related's cash_application_summary.erp_settlement_status."""
        _create_ap_item(db, item_id="REL-SETTLE", state="posted_to_erp", metadata={
            "cash_application_summary": {
                "erp_settlement_status": "pending",
            },
        })
        _create_ap_item(db, item_id="SRC-SETTLE", state="posted_to_erp", metadata={
            "non_invoice_resolution": {
                "related_ap_item_id": "REL-SETTLE",
                "erp_follow_on": {
                    "status": "completed",
                    "action_type": "apply_settlement",
                    "execution_mode": "browser_fallback",
                    "erp_reference": "SET-888",
                },
            },
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 1
        assert result["mismatches"] == 1
        assert result["repaired"] == 1

        related = db.get_ap_item("REL-SETTLE")
        related_meta = json.loads(related["metadata"]) if isinstance(related["metadata"], str) else related["metadata"]
        cash_summary = related_meta["cash_application_summary"]
        assert cash_summary["erp_settlement_status"] == "completed"
        assert cash_summary["erp_settlement_mode"] == "browser_fallback"
        assert cash_summary["erp_settlement_reference"] == "SET-888"
        assert "erp_reconciled_at" in cash_summary

    def test_missing_related_item_skipped(self, db):
        """related_ap_item_id points to nonexistent item — gracefully skipped."""
        _create_ap_item(db, item_id="SRC-ORPHAN", state="posted_to_erp", metadata={
            "non_invoice_resolution": {
                "related_ap_item_id": "DOES-NOT-EXIST",
                "erp_follow_on": {
                    "status": "applied",
                    "action_type": "apply_credit_note",
                },
            },
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["checked"] == 1
        assert result["mismatches"] == 0
        assert result["repaired"] == 0
        assert result["errors"] == 0

    def test_audit_event_recorded(self, db):
        """When a repair is made, an audit event with
        erp_follow_on_reconciliation_repair event_type is appended."""
        _create_ap_item(db, item_id="REL-AUDIT", state="posted_to_erp", metadata={
            "vendor_credit_summary": {
                "erp_application_status": "pending",
            },
        })
        _create_ap_item(db, item_id="SRC-AUDIT", state="posted_to_erp", metadata={
            "non_invoice_resolution": {
                "related_ap_item_id": "REL-AUDIT",
                "erp_follow_on": {
                    "status": "applied",
                    "action_type": "apply_credit_note",
                },
            },
        })

        result = reconcile_erp_follow_on_state(db=db, organization_id="default")
        assert result["repaired"] == 1

        # Check audit events for the related item
        events = db.list_ap_audit_events("REL-AUDIT")
        repair_events = [
            e for e in events
            if e.get("event_type") == "erp_follow_on_reconciliation_repair"
        ]
        assert len(repair_events) == 1
        evt = repair_events[0]
        assert evt["actor_type"] == "system"
        assert evt["actor_id"] == "erp_follow_on_reconciliation"
