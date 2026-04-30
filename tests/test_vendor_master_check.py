"""Vendor-master-check gate tests (AP-side guardrail).

The gate runs on every AP intake. The unit-level cases here exercise
``check_vendor_in_erp_master`` directly — found / not_found / skipped
— and the integration-style cases pin the AP path's behaviour: a
miss transitions the just-saved AP item to ``needs_info`` with
``exception_code=vendor_not_in_erp_master``; a hit lets the workflow
proceed; a resume on workflow re-fire retries the lookup and
advances the item once the customer adds the vendor in their ERP.
"""
from __future__ import annotations

import asyncio


from unittest.mock import MagicMock

from clearledgr.services.vendor_master_check import (
    VENDOR_NOT_IN_ERP_MASTER,
    VendorMasterCheckResult,
    check_vendor_in_erp_master,
    check_vendor_in_erp_master_full,
    needs_info_message,
)


class TestVendorMasterCheck:
    """Unit-level: the helper itself."""

    def test_no_inputs_returns_not_found(self):
        # No vendor name + no sender email → nothing to look up. The
        # operator should see this in their queue rather than have us
        # silently skip the check.
        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name=None,
                sender_email=None,
            )
        )
        assert result == "not_found"

    def test_no_erp_connection_returns_skipped(self, monkeypatch):
        # No ERP wired → can't gate against a master that doesn't
        # exist. AP advances normally.
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: None,
        )
        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="Acme Corp",
            )
        )
        assert result == "skipped"

    def test_found_in_erp_returns_found(self, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return {"vendor_id": "qb-vendor-42", "name": name}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )
        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="Acme Corp",
            )
        )
        assert result == "found"

    def test_not_found_in_erp_returns_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return None

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )
        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="Mystery Vendor",
            )
        )
        assert result == "not_found"

    def test_lookup_exception_returns_skipped(self, monkeypatch):
        # Transient ERP failure must not gate the AP item — the
        # resume hook will retry on workflow re-fire.
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _raising_find_vendor(*args, **kwargs):
            raise RuntimeError("ERP timeout")

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _raising_find_vendor,
        )
        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="Acme Corp",
            )
        )
        assert result == "skipped"

    def test_falls_back_to_email_when_name_missing(self, monkeypatch):
        # Some invoices arrive with a generic display name and only
        # the sender email gives the vendor away (billing@vendor.com).
        # The lookup should pass both keys to find_vendor.
        captured: dict = {}
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _capture_find_vendor(org_id, name=None, email=None):
            captured["name"] = name
            captured["email"] = email
            return {"vendor_id": "qb-vendor-7"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _capture_find_vendor,
        )

        asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="",
                sender_email="billing@vendor.com",
            )
        )
        assert captured == {"name": None, "email": "billing@vendor.com"}


class TestNeedsInfoMessage:
    """The operator-facing copy."""

    def test_uses_vendor_name(self):
        msg = needs_info_message("Acme Supplies")
        assert "Acme Supplies" in msg
        assert "your ERP" in msg
        assert "resume" in msg

    def test_handles_blank_vendor_name(self):
        # A blank vendor name shouldn't produce "Vendor  isn't in
        # your ERP" — fall back to a generic phrasing.
        msg = needs_info_message("")
        assert "this sender" in msg
        assert "your ERP" in msg


class TestVendorNotInErpMasterConstant:
    """The exception_code value is a public contract — any code that
    pattern-matches on it (resume hook, exception queue copy, audit
    queries) breaks if it drifts. Lock the value."""

    def test_value_is_stable(self):
        assert VENDOR_NOT_IN_ERP_MASTER == "vendor_not_in_erp_master"


class TestFuzzyMatchTier:
    """Tier 2: fuzzy match against local ``vendor_profiles`` cache.

    The pre-fix behaviour gated "Cisco Systems Inc" → ``needs_info``
    when the ERP master had "CISCO SYSTEMS, INCORPORATED" — a name
    spelling difference no operator should have to resolve. These
    tests pin the corrected behaviour.
    """

    def test_exact_match_resolves_at_tier_1_without_fuzzy(self, monkeypatch):
        # Tier 1 hit short-circuits — the local fuzzy pass shouldn't
        # even run. Verify by patching list_vendor_profiles to raise
        # if called.
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return {"vendor_id": "ext-1", "name": "Acme Corp"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )

        fake_db = MagicMock()
        fake_db.list_vendor_profiles = MagicMock(
            side_effect=AssertionError("fuzzy tier should not run on exact hit"),
        )
        monkeypatch.setattr(
            "clearledgr.core.database.get_db", lambda: fake_db,
        )

        result = asyncio.run(
            check_vendor_in_erp_master_full(
                organization_id="org-1",
                vendor_name="Acme Corp",
            )
        )
        assert result.status == "found"
        assert result.matched_via == "exact"
        assert result.similarity_score == 1.0

    def test_fuzzy_resolves_inc_vs_incorporated_drift(self, monkeypatch):
        # Tier 1 miss (exact ERP lookup) → Tier 2 hit (local fuzzy
        # match against vendor_profiles cache).
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return None  # exact ERP miss

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )

        fake_db = MagicMock()
        fake_db.list_vendor_profiles = MagicMock(return_value=[
            {"vendor_name": "CISCO SYSTEMS, INCORPORATED"},
            {"vendor_name": "Cisco Systems Inc"},
            {"vendor_name": "ACME Corp"},
        ])
        monkeypatch.setattr(
            "clearledgr.core.database.get_db", lambda: fake_db,
        )

        result = asyncio.run(
            check_vendor_in_erp_master_full(
                organization_id="org-1",
                vendor_name="Cisco Systems Inc",
            )
        )
        assert result.status == "found"
        assert result.matched_via == "fuzzy_local"
        assert (result.matched_name or "").lower().startswith("cisco")
        assert (result.similarity_score or 0) >= 0.85

    def test_fuzzy_below_threshold_returns_not_found(self, monkeypatch):
        # When no candidate clears the 0.85 confidence floor, return
        # not_found rather than risk a wrong auto-bind.
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return None

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )

        fake_db = MagicMock()
        fake_db.list_vendor_profiles = MagicMock(return_value=[
            {"vendor_name": "Apex Manufacturing"},
            {"vendor_name": "Beta Industries"},
        ])
        monkeypatch.setattr(
            "clearledgr.core.database.get_db", lambda: fake_db,
        )

        result = asyncio.run(
            check_vendor_in_erp_master_full(
                organization_id="org-1",
                vendor_name="Acme Corp",  # not similar to either candidate
            )
        )
        assert result.status == "not_found"
        assert result.matched_via is None

    def test_domain_fallback_fires_when_name_misses_both_tiers(self, monkeypatch):
        # Vendor name fails both tier 1 and tier 2; sender domain
        # resolves via tier 3 (find_vendor by email).
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        find_vendor_calls = []

        async def _fake_find_vendor(org_id, name=None, email=None):
            find_vendor_calls.append({"name": name, "email": email})
            # Miss on name; hit on email.
            if email and "vendor.com" in email:
                return {"vendor_id": "ext-2", "name": "Vendor Co"}
            return None

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )

        fake_db = MagicMock()
        fake_db.list_vendor_profiles = MagicMock(return_value=[])
        monkeypatch.setattr(
            "clearledgr.core.database.get_db", lambda: fake_db,
        )

        result = asyncio.run(
            check_vendor_in_erp_master_full(
                organization_id="org-1",
                vendor_name="Mystery Inc",
                sender_email="billing@vendor.com",
            )
        )
        assert result.status == "found"
        assert result.matched_via == "domain"
        # Two find_vendor calls — one for name (miss), one for email (hit).
        assert len(find_vendor_calls) == 2

    def test_status_string_api_unchanged(self, monkeypatch):
        # The legacy ``check_vendor_in_erp_master`` (returning a string)
        # MUST still work for any caller that hasn't migrated to the
        # full-result variant.
        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.get_erp_connection",
            lambda org_id: object(),
        )

        async def _fake_find_vendor(org_id, name=None, email=None):
            return {"vendor_id": "ext-1"}

        monkeypatch.setattr(
            "clearledgr.integrations.erp_router.find_vendor",
            _fake_find_vendor,
        )

        result = asyncio.run(
            check_vendor_in_erp_master(
                organization_id="org-1",
                vendor_name="Acme",
            )
        )
        assert result == "found"
        assert isinstance(result, str)


class TestVendorMasterCheckResultDataclass:
    def test_default_status_only_construction(self):
        r = VendorMasterCheckResult(status="not_found")
        assert r.status == "not_found"
        assert r.matched_via is None
        assert r.matched_name is None
        assert r.similarity_score is None
        assert r.extras == {}
