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


from clearledgr.services.vendor_master_check import (
    VENDOR_NOT_IN_ERP_MASTER,
    check_vendor_in_erp_master,
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
