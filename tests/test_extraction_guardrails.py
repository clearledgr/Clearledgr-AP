"""Tests for the three §7.6 extraction guardrails added to
_evaluate_deterministic_validation:

  - Reference format validation (reference_format_mismatch)
  - Amount range check (amount_outside_vendor_range)
  - PO reference existence (po_reference_not_in_erp)

Each guardrail is exercised via its module-level helper
(``_check_reference_format`` / ``_check_amount_range`` /
``_check_po_exists_in_erp``) using a mock DB fixture. That path
keeps the tests fast and focused; the integration path through
_evaluate_deterministic_validation is covered by the existing
invoice_validation test suite.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from clearledgr.services.invoice_validation import (
    _check_amount_range,
    _check_po_exists_in_erp,
    _check_reference_format,
    _invoice_reference_shape,
)


class TestReferenceShape:
    """Structural pattern extraction used by the reference-format guardrail."""

    def test_prefix_digits(self):
        assert _invoice_reference_shape("INV-2841") == "AAA-####"
        assert _invoice_reference_shape("PO-2041") == "AA-####"
        assert _invoice_reference_shape("INV2841") == "AAA####"

    def test_numeric_only_with_separator(self):
        assert _invoice_reference_shape("2024-001") == "####-###"

    def test_lowercase_normalized(self):
        # lowercase and uppercase reduce to the same shape
        assert _invoice_reference_shape("inv-2841") == _invoice_reference_shape("INV-2841")

    def test_empty_string(self):
        assert _invoice_reference_shape("") == ""


class TestReferenceFormatGuardrail:
    """0d) Reference format validation."""

    def _db(self, history_references):
        db = MagicMock()
        db.get_vendor_invoice_history.return_value = [
            {"invoice_number": ref} for ref in history_references
        ]
        return db

    def test_matching_shape_passes(self):
        db = self._db(["INV-2830", "INV-2831", "INV-2840"])
        result = _check_reference_format(db, "default", "Stripe Inc", "INV-2842")
        assert result is None

    def test_divergent_shape_flags(self):
        # The thesis canonical example: a vendor who always uses
        # INV-XXXX format triggers a PO-2041 extraction.
        db = self._db(["INV-2830", "INV-2831", "INV-2840"])
        result = _check_reference_format(db, "default", "Stripe Inc", "PO-2041")
        assert result is not None
        expected, observed = result
        assert expected == "AAA-####"
        assert observed == "AA-####"

    def test_thin_history_skipped(self):
        # Fewer than 3 historical invoices → no signal to establish pattern.
        db = self._db(["INV-2830", "INV-2831"])
        result = _check_reference_format(db, "default", "Stripe Inc", "PO-2041")
        assert result is None

    def test_mixed_formats_no_dominant_shape(self):
        # Vendor legitimately uses multiple reference formats → don't flag
        # because no pattern covers ≥70% of history.
        db = self._db(["INV-2830", "PO-3001", "REF-4100", "INV-2831", "PO-3002"])
        result = _check_reference_format(db, "default", "Acme", "NEW-9999")
        assert result is None

    def test_no_history_skipped(self):
        db = MagicMock()
        db.get_vendor_invoice_history.return_value = []
        result = _check_reference_format(db, "default", "Acme", "INV-2841")
        assert result is None

    def test_db_error_fails_open(self):
        db = MagicMock()
        db.get_vendor_invoice_history.side_effect = RuntimeError("db down")
        result = _check_reference_format(db, "default", "Acme", "INV-2841")
        assert result is None


class TestAmountRangeGuardrail:
    """0e) Amount range check."""

    def _db(self, amounts, *, approved=True):
        db = MagicMock()
        db.get_vendor_invoice_history.return_value = [
            {
                "amount": amt,
                "was_approved": 1 if approved else 0,
                "final_state": "closed" if approved else "rejected",
            }
            for amt in amounts
        ]
        return db

    def test_within_range_passes(self):
        # Historical max 12000, new invoice 15000 — well within range.
        db = self._db([8000, 10000, 12000, 9500])
        result = _check_amount_range(db, "default", "Stripe Inc", 15000.0)
        assert result is None

    def test_thesis_canonical_breach(self):
        # Thesis example: £12,000 max → £1,200,000 invoice = 100x.
        db = self._db([8000, 10000, 12000, 9500])
        result = _check_amount_range(db, "default", "Stripe Inc", 1_200_000.0)
        assert result is not None
        historical_max, multiplier = result
        assert historical_max == 12000.0
        assert multiplier == pytest.approx(100.0)

    def test_just_over_ceiling(self):
        # Default ceiling is 10x. Exactly 10x passes; 10.1x flags.
        db = self._db([1000, 1000, 1000])
        assert _check_amount_range(db, "default", "Acme", 10000.0) is None
        assert _check_amount_range(db, "default", "Acme", 10100.0) is not None

    def test_thin_history_skipped(self):
        # Fewer than 3 approved invoices → no baseline.
        db = self._db([12000, 10000])
        result = _check_amount_range(db, "default", "Acme", 1_200_000.0)
        assert result is None

    def test_rejected_rows_excluded_from_baseline(self):
        # All history rejected → no usable baseline.
        db = self._db([12000, 10000, 11000], approved=False)
        result = _check_amount_range(db, "default", "Acme", 1_200_000.0)
        assert result is None

    def test_zero_amount_ignored(self):
        db = self._db([1000, 1000, 1000])
        assert _check_amount_range(db, "default", "Acme", 0.0) is None
        assert _check_amount_range(db, "default", "Acme", -5.0) is None

    def test_db_error_fails_open(self):
        db = MagicMock()
        db.get_vendor_invoice_history.side_effect = RuntimeError("db down")
        result = _check_amount_range(db, "default", "Acme", 1_200_000.0)
        assert result is None


class TestPOExistenceGuardrail:
    """0f) PO reference existence in ERP."""

    def test_po_exists_returns_true(self):
        po_service = MagicMock()
        po_service.get_po_by_number.return_value = {"po_id": "PO-123", "status": "open"}
        with patch("clearledgr.services.purchase_orders.get_purchase_order_service",
                   return_value=po_service):
            result = _check_po_exists_in_erp("default", "PO-2041")
        assert result is True

    def test_po_missing_returns_false(self):
        po_service = MagicMock()
        po_service.get_po_by_number.return_value = None
        with patch("clearledgr.services.purchase_orders.get_purchase_order_service",
                   return_value=po_service):
            result = _check_po_exists_in_erp("default", "PO-2041")
        assert result is False

    def test_empty_po_returns_none(self):
        # Empty/whitespace-only po_number can't be checked.
        assert _check_po_exists_in_erp("default", "") is None

    def test_service_error_returns_none(self):
        # Service error → None (fail-open) so the caller decides policy.
        with patch("clearledgr.services.purchase_orders.get_purchase_order_service",
                   side_effect=RuntimeError("erp down")):
            result = _check_po_exists_in_erp("default", "PO-2041")
        assert result is None
