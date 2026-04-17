"""Tests for subscription tier feature gating — DESIGN_THESIS §13.

Covers the tier-comparison table in §13: features that must be on for
each tier, features that must be off, and the annual-discount
arithmetic. Kept separate from the broader subscription service tests
so the thesis-vs-code alignment is a grep-friendly acceptance set.
"""
from __future__ import annotations

import pytest

from clearledgr.services.subscription import (
    PLAN_PRICING,
    PlanFeatures,
    PlanTier,
)


class TestStarterFeatures:
    """§13 tier comparison — Starter row expectations."""

    def _features(self):
        return PlanFeatures.for_tier(PlanTier.STARTER)

    def test_three_way_match_is_included_in_core_ap(self):
        # §13: Starter gets "core AP and Vendor Onboarding workflows."
        # Three-way match is the core AP primitive; selling AP without
        # it is selling invoice scanning.
        assert self._features().three_way_matching is True

    def test_approval_chains_single_tier_only(self):
        # §13: Starter approval routing is "Single tier". Multi-tier
        # (approval_chains) is a Pro/Enterprise differentiator.
        assert self._features().approval_chains is False

    def test_analytics_not_included(self):
        assert self._features().advanced_analytics is False

    def test_api_access_not_included(self):
        assert self._features().api_access is False

    def test_slack_included(self):
        assert self._features().slack_integration is True

    def test_erp_posting_included(self):
        # Part of "core AP".
        assert self._features().erp_posting is True


class TestProfessionalFeatures:
    def _features(self):
        return PlanFeatures.for_tier(PlanTier.PROFESSIONAL)

    def test_three_way_match_included(self):
        assert self._features().three_way_matching is True

    def test_approval_chains_included(self):
        assert self._features().approval_chains is True

    def test_analytics_included(self):
        assert self._features().advanced_analytics is True

    def test_api_access_included(self):
        assert self._features().api_access is True


class TestEnterpriseFeatures:
    def _features(self):
        return PlanFeatures.for_tier(PlanTier.ENTERPRISE)

    def test_everything_professional_has_plus_more(self):
        f = self._features()
        # Every premium feature Professional gets.
        assert f.three_way_matching is True
        assert f.approval_chains is True
        assert f.advanced_analytics is True
        assert f.api_access is True


class TestAnnualDiscountArithmetic:
    """§13 Pricing Structure — 'Annual billing saves 20% on the seat charge.'

    The annual monthly-equivalent price must be 80% of the monthly
    sticker (within $1 for rounding). Previously the annual prices
    rounded to round-number totals and silently drifted to ~17%
    discounts.
    """

    def test_starter_annual_is_20pct_off_monthly(self):
        monthly = PLAN_PRICING[PlanTier.STARTER]["monthly"]
        annual = PLAN_PRICING[PlanTier.STARTER]["annual"]
        expected = monthly * 0.8
        assert abs(annual - expected) <= 0.5, (
            f"Starter annual {annual} is not within $0.50 of {expected:.2f}"
        )

    def test_professional_annual_is_20pct_off_monthly(self):
        monthly = PLAN_PRICING[PlanTier.PROFESSIONAL]["monthly"]
        annual = PLAN_PRICING[PlanTier.PROFESSIONAL]["annual"]
        expected = monthly * 0.8
        assert abs(annual - expected) <= 0.5

    def test_enterprise_annual_is_20pct_off_monthly(self):
        monthly = PLAN_PRICING[PlanTier.ENTERPRISE]["monthly"]
        annual = PLAN_PRICING[PlanTier.ENTERPRISE]["annual"]
        expected = monthly * 0.8
        assert abs(annual - expected) <= 0.5

    def test_free_tier_is_zero_both_cycles(self):
        assert PLAN_PRICING[PlanTier.FREE]["monthly"] == 0
        assert PLAN_PRICING[PlanTier.FREE]["annual"] == 0

    def test_paid_tiers_have_nonzero_annual_price(self):
        # Sanity: the 20% math can't collapse annual to zero for any
        # paid tier (would indicate a bug in the formula).
        for tier in (PlanTier.STARTER, PlanTier.PROFESSIONAL, PlanTier.ENTERPRISE):
            assert PLAN_PRICING[tier]["annual"] > 0
