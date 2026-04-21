"""Tests for the exception resolution agent.

Covers each strategy in ExceptionResolver and the APSkill tool handler.
Uses monkeypatch / mocks to avoid real ERP calls or DB dependencies.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clearledgr.services.exception_resolver import ExceptionResolver, get_exception_resolver

# Common patch targets — lazy imports inside functions mean we patch
# the canonical module, not the caller module.
_PATCH_GET_DB = "clearledgr.core.database.get_db"
_PATCH_LOOKUP_PO = "clearledgr.integrations.erp_router.lookup_purchase_order_from_erp"
_PATCH_FIND_PAYABLES = "clearledgr.integrations.erp_router.find_open_payables_for_vendor"
_PATCH_CREATE_VENDOR = "clearledgr.integrations.erp_router.create_vendor"
_PATCH_GET_RESOLVER = "clearledgr.services.exception_resolver.get_exception_resolver"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ap_item(
    *,
    ap_item_id: str = "AP-001",
    vendor_name: str = "Acme Corp",
    amount: float = 1000.0,
    exception_code: str = "",
    exception_severity: str = "",
    state: str = "needs_approval",
    metadata: Optional[dict] = None,
    **extra,
) -> Dict[str, Any]:
    item = {
        "id": ap_item_id,
        "vendor_name": vendor_name,
        "amount": amount,
        "state": state,
        "exception_code": exception_code,
        "exception_severity": exception_severity,
        "metadata": json.dumps(metadata or {}),
        "currency": "USD",
        "last_error": "",
    }
    item.update(extra)
    return item


def _run(coro):
    """Shortcut for asyncio.run()."""
    return asyncio.run(coro)


class _FakeDB:
    """Minimal mock DB that tracks update_ap_item calls."""

    def __init__(self):
        self.updates: list = []
        self._vendor_profiles: Dict[str, dict] = {}
        self._ap_items: Dict[str, dict] = {}

    def update_ap_item(self, ap_item_id, **kwargs):
        self.updates.append({"ap_item_id": ap_item_id, **kwargs})
        return True

    def get_ap_item(self, ap_item_id):
        return self._ap_items.get(ap_item_id)

    def get_vendor_profile(self, organization_id, vendor_name):
        return self._vendor_profiles.get(vendor_name)

    def _prepare_sql(self, sql):
        return sql

    def connect(self):
        raise RuntimeError("should not be called in tests")


# ---------------------------------------------------------------------------
# ExceptionResolver.resolve dispatch
# ---------------------------------------------------------------------------


class TestResolveDispatch:
    """Tests for the top-level dispatch logic."""

    def test_unknown_exception_code_returns_no_strategy(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()
        result = _run(resolver.resolve(_make_ap_item(), "totally_unknown_code"))
        assert result["resolved"] is False
        assert result["reason"] == "no_strategy_for_exception"
        assert result["exception_code"] == "totally_unknown_code"

    def test_strategy_error_returns_resolved_false(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        # Monkey-patch a strategy to raise
        async def _boom(ap_item, code):
            raise ValueError("kaboom")

        resolver._resolve_missing_po = _boom  # type: ignore[assignment]
        result = _run(resolver.resolve(_make_ap_item(), "po_required_missing"))
        assert result["resolved"] is False
        assert "kaboom" in result["reason"]

    def test_factory_returns_resolver(self):
        resolver = get_exception_resolver("org-test")
        assert isinstance(resolver, ExceptionResolver)
        assert resolver.organization_id == "org-test"


# ---------------------------------------------------------------------------
# Strategy: Missing PO
# ---------------------------------------------------------------------------


class TestMissingPO:
    def test_po_found_via_lookup(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(
            metadata={"extracted_po_number": "PO-999"},
            exception_code="po_required_missing",
        )

        mock_po = {"po_number": "PO-999", "amount": 1000}

        with patch(
            _PATCH_FIND_PAYABLES,
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            _PATCH_LOOKUP_PO,
            new_callable=AsyncMock,
            return_value=mock_po,
        ):
            result = _run(resolver.resolve(item, "po_required_missing"))

        assert result["resolved"] is True
        assert result["action"] == "po_auto_attached"
        assert result["po_number"] == "PO-999"
        assert len(db.updates) == 1
        assert db.updates[0]["po_number"] == "PO-999"
        assert db.updates[0]["exception_code"] is None

    def test_no_po_found_returns_suggestion(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(exception_code="po_required_missing")

        with patch(
            _PATCH_FIND_PAYABLES,
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = _run(resolver.resolve(item, "po_required_missing"))

        assert result["resolved"] is False
        assert result["reason"] == "no_matching_po_in_erp"
        assert "suggestion" in result

    def test_po_found_via_payables(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(exception_code="missing_required_field_po_number")

        with patch(
            _PATCH_FIND_PAYABLES,
            new_callable=AsyncMock,
            return_value=[{"po_number": "PO-123", "amount": 500}],
        ):
            result = _run(resolver.resolve(item, "missing_required_field_po_number"))

        assert result["resolved"] is True
        assert result["po_number"] == "PO-123"
        assert result["source"] == "erp_payables"


# ---------------------------------------------------------------------------
# Strategy: Amount Anomaly (never auto-resolves)
# ---------------------------------------------------------------------------


class TestAmountAnomaly:
    def test_deviation_calculated_with_history(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        db._vendor_profiles["Acme Corp"] = {"avg_invoice_amount": 500.0}
        resolver._db = db

        item = _make_ap_item(amount=1000.0)
        result = _run(resolver.resolve(item, "amount_anomaly_high"))

        assert result["resolved"] is False
        assert result["action"] == "discrepancy_calculated"
        assert result["invoice_amount"] == 1000.0
        assert result["vendor_average"] == 500.0
        assert result["deviation_percent"] == 100.0
        assert "above" in result["suggestion"]

    def test_no_vendor_history(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(amount=1000.0)
        result = _run(resolver.resolve(item, "amount_anomaly_moderate"))

        assert result["resolved"] is False
        assert result["reason"] == "no_vendor_history"

    def test_below_average(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        db._vendor_profiles["Acme Corp"] = {"avg_invoice_amount": 2000.0}
        resolver._db = db

        item = _make_ap_item(amount=500.0)
        result = _run(resolver.resolve(item, "amount_anomaly_high"))

        assert result["resolved"] is False
        assert "below" in result["suggestion"]


# ---------------------------------------------------------------------------
# Strategy: Vendor Not Found (auto-resolves on success)
# ---------------------------------------------------------------------------


class TestVendorNotFound:
    def test_vendor_created_successfully(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(exception_code="erp_vendor_not_found")

        with patch(
            _PATCH_CREATE_VENDOR,
            new_callable=AsyncMock,
            return_value={"status": "success", "vendor_id": "V-42"},
        ):
            result = _run(resolver.resolve(item, "erp_vendor_not_found"))

        assert result["resolved"] is True
        assert result["action"] == "vendor_created_in_erp"
        assert result["vendor_id"] == "V-42"
        assert len(db.updates) == 1
        assert db.updates[0]["exception_code"] is None

    def test_vendor_creation_fails(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(exception_code="erp_vendor_not_found")

        with patch(
            _PATCH_CREATE_VENDOR,
            new_callable=AsyncMock,
            side_effect=RuntimeError("ERP down"),
        ):
            result = _run(resolver.resolve(item, "erp_vendor_not_found"))

        assert result["resolved"] is False
        assert "vendor_creation_failed" in result["reason"]
        assert len(db.updates) == 0

    def test_vendor_creation_no_id_returned(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(exception_code="erp_vendor_not_found")

        with patch(
            _PATCH_CREATE_VENDOR,
            new_callable=AsyncMock,
            return_value={"status": "error", "reason": "quota_exceeded"},
        ):
            result = _run(resolver.resolve(item, "erp_vendor_not_found"))

        assert result["resolved"] is False
        assert result["reason"] == "vendor_creation_returned_no_id"

    def test_no_vendor_name(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        resolver._db = db

        item = _make_ap_item(vendor_name="")
        result = _run(resolver.resolve(item, "erp_vendor_not_found"))

        assert result["resolved"] is False
        assert result["reason"] == "no_vendor_name_on_item"


# ---------------------------------------------------------------------------
# Strategy: Duplicate Invoice (never auto-resolves)
# ---------------------------------------------------------------------------


class TestDuplicateInvoice:
    def test_original_found(self):
        resolver = ExceptionResolver("org-1")
        db = _FakeDB()
        db._ap_items["AP-ORIG"] = {
            "id": "AP-ORIG",
            "state": "posted_to_erp",
            "amount": 1000.0,
        }
        resolver._db = db

        item = _make_ap_item(
            metadata={"duplicate_ap_item_id": "AP-ORIG"},
            exception_code="duplicate_invoice",
        )
        result = _run(resolver.resolve(item, "duplicate_invoice"))

        assert result["resolved"] is False
        assert result["action"] == "duplicate_identified"
        assert result["original_ap_item_id"] == "AP-ORIG"
        assert result["original_state"] == "posted_to_erp"
        assert "Reject" in result["suggestion"]

    def test_no_original_in_metadata(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(exception_code="erp_duplicate_bill")
        result = _run(resolver.resolve(item, "erp_duplicate_bill"))

        assert result["resolved"] is False
        assert result["reason"] == "no_original_found"


# ---------------------------------------------------------------------------
# Strategy: Low Confidence Fields (never auto-resolves)
# ---------------------------------------------------------------------------


class TestLowConfidence:
    def test_blockers_identified(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(
            metadata={
                "confidence_gate": {
                    "confidence_blockers": [
                        {"field": "amount", "confidence": 0.55},
                        {"field": "vendor_name", "confidence": 0.60},
                    ]
                }
            },
            exception_code="confidence_field_review_required",
        )
        result = _run(resolver.resolve(item, "confidence_field_review_required"))

        assert result["resolved"] is False
        assert result["action"] == "fields_identified"
        assert len(result["low_confidence_fields"]) == 2
        assert result["low_confidence_fields"][0]["field"] == "amount"
        assert "2 field(s)" in result["suggestion"]

    def test_no_blockers(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(exception_code="confidence_field_review_required")
        result = _run(resolver.resolve(item, "confidence_field_review_required"))

        assert result["resolved"] is False
        assert result["action"] == "fields_identified"
        assert len(result["low_confidence_fields"]) == 0


# ---------------------------------------------------------------------------
# Strategy: Currency Mismatch (never auto-resolves)
# ---------------------------------------------------------------------------


class TestCurrencyMismatch:
    def test_mismatch_surfaced(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(
            metadata={"expected_currency": "EUR"},
            exception_code="currency_mismatch",
        )
        item["currency"] = "USD"
        result = _run(resolver.resolve(item, "currency_mismatch"))

        assert result["resolved"] is False
        assert result["action"] == "currency_mismatch_surfaced"
        assert result["invoice_currency"] == "USD"
        assert result["expected_currency"] == "EUR"


# ---------------------------------------------------------------------------
# Strategy: Vendor Mismatch (never auto-resolves)
# ---------------------------------------------------------------------------


class TestVendorMismatch:
    def test_similar_vendor_found(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        # Monkey-patch the vendor name listing
        resolver._list_vendor_names = lambda: ["Acme Corporation", "Beta Inc", "Gamma LLC"]

        item = _make_ap_item(vendor_name="Acme Corp", exception_code="vendor_mismatch")
        result = _run(resolver.resolve(item, "vendor_mismatch"))

        assert result["resolved"] is False
        assert result["action"] == "vendor_suggestion"
        assert result["suggested_vendor"] == "Acme Corporation"
        assert result["match_score"] > 0.6

    def test_no_similar_vendors(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()
        resolver._list_vendor_names = lambda: ["Totally Different"]

        item = _make_ap_item(vendor_name="Acme Corp", exception_code="vendor_mismatch")
        result = _run(resolver.resolve(item, "vendor_mismatch"))

        assert result["resolved"] is False
        assert result["reason"] == "no_similar_vendors_found"

    def test_no_known_vendors(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()
        resolver._list_vendor_names = lambda: []

        item = _make_ap_item(exception_code="vendor_mismatch")
        result = _run(resolver.resolve(item, "vendor_mismatch"))

        assert result["resolved"] is False
        assert result["reason"] == "no_known_vendors"


# ---------------------------------------------------------------------------
# Strategy: Vendor Unresponsive (never auto-resolves)
# ---------------------------------------------------------------------------


class TestVendorUnresponsive:
    def test_escalation_suggested(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(
            metadata={
                "followup_attempt_count": 3,
                "followup_sent_at": "2026-03-25T10:00:00Z",
            },
            exception_code="vendor_unresponsive",
        )
        result = _run(resolver.resolve(item, "vendor_unresponsive"))

        assert result["resolved"] is False
        assert result["action"] == "escalation_suggested"
        assert result["followup_attempts"] == 3


# ---------------------------------------------------------------------------
# Strategy: Posting Exhausted (never auto-resolves)
# ---------------------------------------------------------------------------


class TestPostingExhausted:
    def test_posting_failure_surfaced(self):
        resolver = ExceptionResolver("org-1")
        resolver._db = _FakeDB()

        item = _make_ap_item(
            exception_code="posting_exhausted",
            last_error="Connection refused",
            metadata={"erp_post_attempts": 3},
        )
        result = _run(resolver.resolve(item, "posting_exhausted"))

        assert result["resolved"] is False
        assert result["action"] == "posting_failure_surfaced"
        assert result["retry_count"] == 3
        assert "Connection refused" in result["suggestion"]


# ---------------------------------------------------------------------------
# Background sweep
# ---------------------------------------------------------------------------


class TestBackgroundSweep:
    def test_sweep_resolves_items(self):
        from clearledgr.services import agent_background as bg_mod

        mock_db = MagicMock()
        items_with_exception = [
            _make_ap_item(ap_item_id="AP-1", exception_code="erp_vendor_not_found"),
            _make_ap_item(ap_item_id="AP-2", exception_code="amount_anomaly_high"),
        ]
        # Return exception items only for the first state, empty for rest
        call_count = {"n": 0}

        def _list_ap_items(org_id, state=None, limit=50):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return items_with_exception
            return []

        mock_db.list_ap_items = _list_ap_items
        mock_db.append_audit_event = MagicMock(return_value=None)

        mock_resolver = MagicMock()
        mock_resolver.resolve = AsyncMock(
            side_effect=[
                {"resolved": True, "action": "vendor_created_in_erp"},
                {"resolved": False, "reason": "no_vendor_history"},
            ]
        )

        with patch(_PATCH_GET_DB, return_value=mock_db):
            with patch(
                _PATCH_GET_RESOLVER,
                return_value=mock_resolver,
            ):
                _run(bg_mod._sweep_exception_resolutions("org-1"))

        # resolver called for both items
        assert mock_resolver.resolve.call_count == 2
        # audit event logged for the resolved one
        assert mock_db.append_audit_event.call_count == 1

    def test_sweep_caps_at_25(self):
        from clearledgr.services import agent_background as bg_mod

        mock_db = MagicMock()
        # Return 30 items for the first state
        items = [
            _make_ap_item(ap_item_id=f"AP-{i}", exception_code="amount_anomaly_high")
            for i in range(30)
        ]
        call_count = {"n": 0}

        def _list_ap_items(org_id, state=None, limit=50):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return items
            return []

        mock_db.list_ap_items = _list_ap_items
        mock_db.append_audit_event = MagicMock()

        mock_resolver = MagicMock()
        mock_resolver.resolve = AsyncMock(
            return_value={"resolved": False, "reason": "no_vendor_history"}
        )

        with patch(_PATCH_GET_DB, return_value=mock_db):
            with patch(
                _PATCH_GET_RESOLVER,
                return_value=mock_resolver,
            ):
                _run(bg_mod._sweep_exception_resolutions("org-1"))

        # Should be capped at 25
        assert mock_resolver.resolve.call_count == 25

    def test_sweep_skips_items_without_exception_code(self):
        from clearledgr.services import agent_background as bg_mod

        mock_db = MagicMock()
        items = [
            _make_ap_item(ap_item_id="AP-1", exception_code=""),
            _make_ap_item(ap_item_id="AP-2", exception_code="amount_anomaly_high"),
        ]
        call_count = {"n": 0}

        def _list_ap_items(org_id, state=None, limit=50):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return items
            return []

        mock_db.list_ap_items = _list_ap_items
        mock_db.append_audit_event = MagicMock()

        mock_resolver = MagicMock()
        mock_resolver.resolve = AsyncMock(
            return_value={"resolved": False, "reason": "no_vendor_history"}
        )

        with patch(_PATCH_GET_DB, return_value=mock_db):
            with patch(
                _PATCH_GET_RESOLVER,
                return_value=mock_resolver,
            ):
                _run(bg_mod._sweep_exception_resolutions("org-1"))

        # Only the item with exception_code should be resolved
        assert mock_resolver.resolve.call_count == 1

    def test_sweep_handles_db_error_gracefully(self):
        from clearledgr.services import agent_background as bg_mod

        with patch(
            _PATCH_GET_DB,
            side_effect=RuntimeError("db down"),
        ):
            # Should not raise
            _run(bg_mod._sweep_exception_resolutions("org-1"))


# ---------------------------------------------------------------------------
# Parse metadata helper
# ---------------------------------------------------------------------------


class TestParseMetadata:
    def test_dict_passthrough(self):
        item = {"metadata": {"key": "val"}}
        result = ExceptionResolver._parse_metadata(item)
        assert result == {"key": "val"}

    def test_json_string(self):
        item = {"metadata": '{"key": "val"}'}
        result = ExceptionResolver._parse_metadata(item)
        assert result == {"key": "val"}

    def test_empty_string(self):
        result = ExceptionResolver._parse_metadata({"metadata": ""})
        assert result == {}

    def test_none(self):
        result = ExceptionResolver._parse_metadata({"metadata": None})
        assert result == {}

    def test_invalid_json(self):
        result = ExceptionResolver._parse_metadata({"metadata": "not json"})
        assert result == {}
