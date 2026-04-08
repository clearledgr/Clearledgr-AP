"""Tests for early payment discount optimization.

Covers parse_discount_terms, calculate_discount_opportunity, expired discounts,
and annualized return calculations.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.services.discount_optimizer import (  # noqa: E402
    parse_discount_terms,
    calculate_discount_opportunity,
)


# ---------------------------------------------------------------------------
# parse_discount_terms
# ---------------------------------------------------------------------------


class TestParseDiscountTerms:
    def test_parses_2_10_net_30(self):
        result = parse_discount_terms("2/10 NET 30")
        assert result is not None
        assert result["discount_pct"] == 2.0
        assert result["discount_days"] == 10
        assert result["net_days"] == 30

    def test_parses_1_5_15_net_45(self):
        result = parse_discount_terms("1.5/15 NET 45")
        assert result is not None
        assert result["discount_pct"] == 1.5
        assert result["discount_days"] == 15
        assert result["net_days"] == 45

    def test_parses_lowercase_net(self):
        result = parse_discount_terms("3/10 net 60")
        assert result is not None
        assert result["discount_pct"] == 3.0
        assert result["net_days"] == 60

    def test_returns_none_for_no_discount_terms(self):
        assert parse_discount_terms("Net 30") is None
        assert parse_discount_terms("") is None
        assert parse_discount_terms(None) is None

    def test_returns_none_for_random_text(self):
        assert parse_discount_terms("Due upon receipt") is None
        assert parse_discount_terms("Please pay ASAP") is None

    def test_parses_with_extra_spaces(self):
        result = parse_discount_terms("2 / 10  NET  30")
        assert result is not None
        assert result["discount_pct"] == 2.0


# ---------------------------------------------------------------------------
# calculate_discount_opportunity
# ---------------------------------------------------------------------------


class TestCalculateDiscountOpportunity:
    def test_correct_annualized_return_2_10_net_30(self):
        """2% discount for paying 20 days early = 36.7% annualized."""
        result = calculate_discount_opportunity(
            amount=10_000.0,
            payment_terms="2/10 NET 30",
        )
        assert result is not None
        assert result["has_discount"] is True
        assert result["discount_pct"] == 2.0
        assert result["discount_amount"] == 200.0
        assert result["net_amount_if_discounted"] == 9800.0
        assert result["days_saved"] == 20
        # Annualized: (0.02 / 0.98) * (365/20) ≈ 37.2%
        assert 36.0 <= result["annualized_return_pct"] <= 38.0

    def test_returns_none_for_no_discount_terms(self):
        result = calculate_discount_opportunity(
            amount=5000.0,
            payment_terms="Net 30",
        )
        assert result is None

    def test_currency_passed_through(self):
        result = calculate_discount_opportunity(
            amount=1000.0,
            payment_terms="2/10 NET 30",
            currency="EUR",
        )
        assert result["currency"] == "EUR"

    def test_with_invoice_date_computes_deadline(self):
        """When invoice_date is given, a discount_deadline should be computed."""
        result = calculate_discount_opportunity(
            amount=5000.0,
            payment_terms="2/10 NET 30",
            invoice_date="2026-04-01",
        )
        assert result is not None
        assert result["discount_deadline"] == "2026-04-11"

    def test_expired_discount_detected(self):
        """An invoice dated far in the past should show is_expired=True."""
        past_date = (date.today() - timedelta(days=60)).isoformat()
        result = calculate_discount_opportunity(
            amount=5000.0,
            payment_terms="2/10 NET 30",
            invoice_date=past_date,
        )
        assert result is not None
        assert result["is_expired"] is True
        assert result["recommendation"] == "DISCOUNT EXPIRED"

    def test_active_discount_with_high_return_recommends_take(self):
        """An active discount with high annualized return should say TAKE DISCOUNT."""
        future_date = date.today().isoformat()
        result = calculate_discount_opportunity(
            amount=10_000.0,
            payment_terms="2/10 NET 30",
            invoice_date=future_date,
        )
        assert result is not None
        assert result["is_expired"] is False
        assert result["recommendation"] == "TAKE DISCOUNT"

    def test_simple_percentage_discount_terms(self):
        """Handles '2% early payment discount' syntax."""
        result = calculate_discount_opportunity(
            amount=10_000.0,
            payment_terms="2% early payment discount",
        )
        assert result is not None
        assert result["discount_pct"] == 2.0

    def test_summary_contains_amounts(self):
        result = calculate_discount_opportunity(
            amount=10_000.0,
            payment_terms="2/10 NET 30",
            currency="USD",
        )
        assert result is not None
        assert "9,800.00" in result["summary"]
        assert "200.00" in result["summary"]
        assert "USD" in result["summary"]
