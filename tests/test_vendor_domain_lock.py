"""Tests for Phase 2.2 — vendor domain lock (DESIGN_THESIS.md §8).

Covers:
  - Pure helpers: extract_sender_domain, is_payment_processor,
    domain_matches_allowlist (including subdomain + fake-prefix + case)
  - VendorStore typed accessors: get / add / remove / ensure_tracked
  - VendorDomainLockService: check_sender_domain across all status
    codes (match, mismatch, processor_bypass, no_known_domains,
    no_sender, no_vendor), audit events on add/remove,
    record_domain_on_first_post
  - Validation gate: vendor_sender_domain_mismatch reason code fires
    with severity=error, doesn't fire on bootstrap, doesn't fire on
    processors, doesn't fire on match
  - VendorDomainTrackingObserver: records on first post, no-op on
    subsequent posts, no-op when sender is a processor
  - REST API: GET read, POST add (CFO required, idempotent), DELETE
    remove (404 on missing), cross-tenant blocked, non-CFO blocked
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest


# ===========================================================================
# Pure helpers
# ===========================================================================


class TestExtractSenderDomain:

    def test_bare_email(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("billing@acme.com") == "acme.com"

    def test_with_display_name(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("Acme Corp <billing@acme.com>") == "acme.com"

    def test_with_quoted_display_name(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert (
            extract_sender_domain('"Acme Corp" <billing@acme.com>') == "acme.com"
        )

    def test_uppercase_normalized(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("BILLING@ACME.COM") == "acme.com"

    def test_subdomain_preserved(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("ap@billing.acme.com") == "billing.acme.com"

    def test_whitespace_stripped(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("  billing@acme.com  ") == "acme.com"

    def test_empty_and_none(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("") == ""
        assert extract_sender_domain(None) == ""

    def test_no_at_sign(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        assert extract_sender_domain("not-an-email") == ""

    def test_strips_display_injection(self):
        from clearledgr.services.vendor_domain_lock import extract_sender_domain
        # Chars that can't appear in a valid DNS label get stripped
        assert (
            extract_sender_domain("billing@acme.com;evil=1") == "acme.comevil1"
        )


class TestIsPaymentProcessor:

    def test_known_processor(self):
        from clearledgr.services.vendor_domain_lock import is_payment_processor
        assert is_payment_processor("stripe.com") is True
        assert is_payment_processor("paypal.com") is True
        assert is_payment_processor("bill.com") is True

    def test_processor_subdomain(self):
        from clearledgr.services.vendor_domain_lock import is_payment_processor
        assert is_payment_processor("mail.stripe.com") is True

    def test_not_a_processor(self):
        from clearledgr.services.vendor_domain_lock import is_payment_processor
        assert is_payment_processor("acme.com") is False

    def test_empty(self):
        from clearledgr.services.vendor_domain_lock import is_payment_processor
        assert is_payment_processor("") is False


class TestDomainMatchesAllowlist:

    def test_exact_match(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("acme.com", ["acme.com"]) is True

    def test_subdomain_match(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("billing.acme.com", ["acme.com"]) is True
        assert domain_matches_allowlist("ap.billing.acme.com", ["acme.com"]) is True

    def test_fake_prefix_rejected(self):
        """fake-acme.com must NOT match acme.com — the distinctive
        security property of dot-boundary suffix matching."""
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("fake-acme.com", ["acme.com"]) is False
        assert domain_matches_allowlist("notacme.com", ["acme.com"]) is False

    def test_fake_suffix_rejected(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("acme.com.evil", ["acme.com"]) is False

    def test_case_insensitive(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("ACME.COM", ["acme.com"]) is True
        assert domain_matches_allowlist("acme.com", ["ACME.COM"]) is True

    def test_multi_entry_allowlist(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        allowlist = ["acme.com", "acme.io", "acme-trading.co.uk"]
        assert domain_matches_allowlist("acme.io", allowlist) is True
        assert domain_matches_allowlist("billing.acme-trading.co.uk", allowlist) is True
        assert domain_matches_allowlist("unknown.com", allowlist) is False

    def test_empty_allowlist_no_match(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("acme.com", []) is False

    def test_empty_sender_no_match(self):
        from clearledgr.services.vendor_domain_lock import domain_matches_allowlist
        assert domain_matches_allowlist("", ["acme.com"]) is False


# ===========================================================================
# VendorStore typed accessors
# ===========================================================================


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from clearledgr.core.database import get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
    return db


def _seed_vendor(db, vendor="Acme", domains=None, invoice_count=5):
    db.create_organization("org_t", name="X")
    kwargs: Dict[str, Any] = {"invoice_count": invoice_count}
    if domains is not None:
        kwargs["sender_domains"] = domains
    db.upsert_vendor_profile("org_t", vendor, **kwargs)


class TestVendorStoreTrustedDomains:

    def test_empty_when_no_domains(self, tmp_db):
        _seed_vendor(tmp_db)
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == []

    def test_add_normalizes_case_and_whitespace(self, tmp_db):
        _seed_vendor(tmp_db)
        assert tmp_db.add_trusted_sender_domain("org_t", "Acme", "  ACME.COM  ") is True
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_add_idempotent(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.add_trusted_sender_domain("org_t", "Acme", "acme.com")
        tmp_db.add_trusted_sender_domain("org_t", "Acme", "acme.com")
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_add_multiple_preserves_order(self, tmp_db):
        _seed_vendor(tmp_db)
        tmp_db.add_trusted_sender_domain("org_t", "Acme", "acme.com")
        tmp_db.add_trusted_sender_domain("org_t", "Acme", "acme.io")
        tmp_db.add_trusted_sender_domain("org_t", "Acme", "acme.co.uk")
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == [
            "acme.com", "acme.io", "acme.co.uk",
        ]

    def test_remove_existing(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com", "acme.io"])
        assert tmp_db.remove_trusted_sender_domain("org_t", "Acme", "acme.io") is True
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_remove_missing_returns_false(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        assert tmp_db.remove_trusted_sender_domain("org_t", "Acme", "evil.com") is False

    def test_remove_case_insensitive(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        assert tmp_db.remove_trusted_sender_domain("org_t", "Acme", "ACME.COM") is True
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == []

    def test_ensure_tracked_first_sighting(self, tmp_db):
        _seed_vendor(tmp_db)
        assert (
            tmp_db.ensure_trusted_sender_domain_tracked("org_t", "Acme", "acme.com")
            is True
        )
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_ensure_tracked_no_op_when_already_has_domains(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        # New sighting from a different domain is NOT auto-added
        assert (
            tmp_db.ensure_trusted_sender_domain_tracked("org_t", "Acme", "evil.com")
            is False
        )
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_get_unknown_vendor_returns_empty(self, tmp_db):
        tmp_db.create_organization("org_t", name="X")
        assert tmp_db.get_trusted_sender_domains("org_t", "Ghost") == []


# ===========================================================================
# VendorDomainLockService
# ===========================================================================


class TestVendorDomainLockService:

    def _svc(self, db):
        from clearledgr.services.vendor_domain_lock import (
            VendorDomainLockService,
        )
        return VendorDomainLockService("org_t", db=db)

    def test_match_with_exact_domain(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="billing@acme.com"
        )
        assert result.status == "match"
        assert result.should_block is False

    def test_match_with_subdomain(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="ap@billing.acme.com"
        )
        assert result.status == "match"
        assert result.should_block is False

    def test_mismatch_blocks(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="billing@evil.com"
        )
        assert result.status == "mismatch"
        assert result.should_block is True
        assert result.sender_domain == "evil.com"
        assert result.known_domains == ["acme.com"]

    def test_mismatch_fake_prefix(self, tmp_db):
        """Critical security case: fake-acme.com must not match acme.com."""
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="billing@fake-acme.com"
        )
        assert result.status == "mismatch"
        assert result.should_block is True

    def test_processor_bypass(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="invoice@stripe.com"
        )
        assert result.status == "processor_bypass"
        assert result.should_block is False

    def test_no_known_domains_skips_check(self, tmp_db):
        """Bootstrap path — vendor has no domains yet, so the check
        returns no_known_domains (covered by first-payment-hold)."""
        _seed_vendor(tmp_db, domains=[])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender="billing@whoever.com"
        )
        assert result.status == "no_known_domains"
        assert result.should_block is False

    def test_no_sender(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name="Acme", sender=None
        )
        assert result.status == "no_sender"
        assert result.should_block is False

    def test_no_vendor_name(self, tmp_db):
        result = self._svc(tmp_db).check_sender_domain(
            vendor_name=None, sender="billing@acme.com"
        )
        assert result.status == "no_vendor"
        assert result.should_block is False

    def test_add_emits_audit_event(self, tmp_db):
        _seed_vendor(tmp_db)
        self._svc(tmp_db).add_trusted_domain(
            vendor_name="Acme",
            domain="acme.com",
            actor_id="cfo@test",
        )
        events = tmp_db.list_recent_ap_audit_events("org_t", limit=50)
        added = [e for e in events if e.get("event_type") == "vendor_trusted_domain_added"]
        assert len(added) == 1
        assert added[0]["actor_id"] == "cfo@test"

    def test_add_duplicate_does_not_emit_audit(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        self._svc(tmp_db).add_trusted_domain(
            vendor_name="Acme",
            domain="acme.com",
            actor_id="cfo@test",
        )
        events = tmp_db.list_recent_ap_audit_events("org_t", limit=50)
        added = [e for e in events if e.get("event_type") == "vendor_trusted_domain_added"]
        assert len(added) == 0

    def test_remove_emits_audit_event(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        self._svc(tmp_db).remove_trusted_domain(
            vendor_name="Acme",
            domain="acme.com",
            actor_id="cfo@test",
        )
        events = tmp_db.list_recent_ap_audit_events("org_t", limit=50)
        removed = [e for e in events if e.get("event_type") == "vendor_trusted_domain_removed"]
        assert len(removed) == 1

    def test_record_domain_on_first_post_bootstraps(self, tmp_db):
        _seed_vendor(tmp_db)
        svc = self._svc(tmp_db)
        recorded = svc.record_domain_on_first_post(
            vendor_name="Acme", sender="billing@acme.com"
        )
        assert recorded is True
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_record_domain_no_op_when_allowlist_populated(self, tmp_db):
        _seed_vendor(tmp_db, domains=["acme.com"])
        svc = self._svc(tmp_db)
        recorded = svc.record_domain_on_first_post(
            vendor_name="Acme", sender="billing@evil.com"
        )
        assert recorded is False
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_record_domain_skips_processor_senders(self, tmp_db):
        _seed_vendor(tmp_db)
        svc = self._svc(tmp_db)
        recorded = svc.record_domain_on_first_post(
            vendor_name="Acme", sender="invoice@stripe.com"
        )
        assert recorded is False
        # Don't pollute the allowlist with processor domains
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == []


# ===========================================================================
# Validation gate integration
# ===========================================================================


class TestValidationGateDomainLock:

    def _seed_fully(self, db, domains=None):
        """Seed a vendor that passes all other fraud-control gates."""
        db.create_organization("org_t", name="X")
        kwargs = {
            "invoice_count": 5,
            "avg_invoice_amount": 10_000.0,
            "always_approved": 1,
            "last_invoice_date": (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).isoformat(),
        }
        if domains is not None:
            kwargs["sender_domains"] = domains
        db.upsert_vendor_profile("org_t", "Acme", **kwargs)
        db.set_vendor_bank_details(
            "org_t", "Acme",
            {"iban": "GB82WEST12345698765432", "account_number": "12345678"},
        )

    def _make_invoice(self, sender="billing@acme.com"):
        from clearledgr.services.invoice_models import InvoiceData
        return InvoiceData(
            gmail_id="gmail-domain-test",
            subject="Invoice",
            sender=sender,
            vendor_name="Acme",
            amount=1000.0,
            currency="GBP",
            invoice_number="INV-1",
            due_date="2026-05-01",
            confidence=0.97,
            organization_id="org_t",
            field_confidences={
                "vendor": 0.99,
                "amount": 0.98,
                "invoice_number": 0.97,
                "due_date": 0.95,
            },
        )

    def test_matching_domain_no_block(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        self._seed_fully(tmp_db, domains=["acme.com"])
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(sender="billing@acme.com")
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "vendor_sender_domain_mismatch" not in gate["reason_codes"]

    def test_mismatching_domain_blocks(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        self._seed_fully(tmp_db, domains=["acme.com"])
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(sender="billing@fake-acme.com")
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "vendor_sender_domain_mismatch" in gate["reason_codes"]
        assert gate["passed"] is False
        reason = next(
            r for r in gate["reasons"] if r["code"] == "vendor_sender_domain_mismatch"
        )
        assert reason["severity"] == "error"
        assert reason["details"]["sender_domain"] == "fake-acme.com"
        assert reason["details"]["trusted_domains"] == ["acme.com"]

    def test_bootstrap_no_block_when_no_known_domains(self, tmp_db):
        """Vendors with no known domains yet are protected by
        first-payment-hold, not by the domain lock."""
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        self._seed_fully(tmp_db, domains=[])
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(sender="billing@anywhere.com")
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "vendor_sender_domain_mismatch" not in gate["reason_codes"]

    def test_processor_bypass_no_block(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        self._seed_fully(tmp_db, domains=["acme.com"])
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(sender="invoice@stripe.com")
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "vendor_sender_domain_mismatch" not in gate["reason_codes"]

    def test_subdomain_matches_no_block(self, tmp_db):
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService
        self._seed_fully(tmp_db, domains=["acme.com"])
        service = InvoiceWorkflowService(organization_id="org_t")
        invoice = self._make_invoice(sender="ap@billing.acme.com")
        gate = asyncio.run(service._evaluate_deterministic_validation(invoice))
        assert "vendor_sender_domain_mismatch" not in gate["reason_codes"]


# ===========================================================================
# VendorDomainTrackingObserver
# ===========================================================================


class TestVendorDomainTrackingObserver:

    def _make_event(self, **overrides):
        from clearledgr.services.state_observers import StateTransitionEvent
        defaults = dict(
            ap_item_id="AP-OBS-1",
            organization_id="org_t",
            old_state="ready_to_post",
            new_state="posted_to_erp",
        )
        defaults.update(overrides)
        return StateTransitionEvent(**defaults)

    def _seed_posted_item(
        self, db, *, sender="billing@acme.com", vendor="Acme", ap_item_id="AP-OBS-1"
    ):
        db.create_organization("org_t", name="X")
        db.create_ap_item(
            {
                "id": ap_item_id,
                "organization_id": "org_t",
                "vendor_name": vendor,
                "sender": sender,
                "amount": 1000.0,
                "currency": "USD",
                "state": "posted_to_erp",
                "thread_id": f"gmail-{ap_item_id}",
                "invoice_number": f"INV-{ap_item_id}",
            }
        )
        db.upsert_vendor_profile("org_t", vendor, invoice_count=1)

    def test_observer_records_domain_on_first_post(self, tmp_db):
        from clearledgr.services.state_observers import VendorDomainTrackingObserver
        self._seed_posted_item(tmp_db)
        observer = VendorDomainTrackingObserver(tmp_db)
        asyncio.run(observer.on_transition(self._make_event()))
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_observer_skips_when_domain_already_known(self, tmp_db):
        from clearledgr.services.state_observers import VendorDomainTrackingObserver
        tmp_db.create_organization("org_t", name="X")
        tmp_db.upsert_vendor_profile(
            "org_t", "Acme", invoice_count=5, sender_domains=["acme.com"]
        )
        tmp_db.create_ap_item(
            {
                "id": "AP-OBS-2",
                "organization_id": "org_t",
                "vendor_name": "Acme",
                "sender": "evil@different-acme.com",
                "amount": 500.0,
                "state": "posted_to_erp",
                "thread_id": "gmail-obs-2",
                "invoice_number": "INV-2",
            }
        )
        observer = VendorDomainTrackingObserver(tmp_db)
        asyncio.run(observer.on_transition(self._make_event(ap_item_id="AP-OBS-2")))
        # Allowlist unchanged — observer does not expand established vendors
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == ["acme.com"]

    def test_observer_skips_non_posted_state(self, tmp_db):
        from clearledgr.services.state_observers import VendorDomainTrackingObserver
        self._seed_posted_item(tmp_db)
        observer = VendorDomainTrackingObserver(tmp_db)
        asyncio.run(
            observer.on_transition(self._make_event(new_state="approved"))
        )
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == []

    def test_observer_skips_processor_sender(self, tmp_db):
        from clearledgr.services.state_observers import VendorDomainTrackingObserver
        self._seed_posted_item(tmp_db, sender="invoice@stripe.com")
        observer = VendorDomainTrackingObserver(tmp_db)
        asyncio.run(observer.on_transition(self._make_event()))
        # Don't pollute allowlist with processor domains
        assert tmp_db.get_trusted_sender_domains("org_t", "Acme") == []


# ===========================================================================
# REST API
# ===========================================================================


class TestVendorTrustedDomainsAPI:

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from clearledgr.core.database import get_db
        from clearledgr.core import database as db_module
        import main

        db = get_db()
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _override_user(self, main, role: str, org_id: str = "org_t"):
        from clearledgr.core.auth import (
            TokenData,
            get_current_user,
            require_cfo,
        )
        from datetime import datetime, timezone

        def _user():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id=org_id,
                role=role,
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        main.app.dependency_overrides[require_cfo] = _user

    def test_get_empty_allowlist(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "user")
        try:
            resp = client.get(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t"
            )
            assert resp.status_code == 200
            assert resp.json()["trusted_domains"] == []
        finally:
            main.app.dependency_overrides.clear()

    def test_get_with_domains(self, app_client):
        client, main, db = app_client
        _seed_vendor(db, domains=["acme.com", "acme.io"])
        self._override_user(main, "user")
        try:
            resp = client.get(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["trusted_domains"] == ["acme.com", "acme.io"]
            assert body["vendor_name"] == "Acme"
        finally:
            main.app.dependency_overrides.clear()

    def test_post_add_happy_path(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "cfo")
        try:
            resp = client.post(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t",
                json={"domain": "acme.com"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "added"
            assert body["trusted_domains"] == ["acme.com"]
        finally:
            main.app.dependency_overrides.clear()

    def test_post_add_requires_cfo_role(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        from clearledgr.core.auth import TokenData, get_current_user
        from datetime import datetime, timezone

        def _user():
            return TokenData(
                user_id="u1",
                email="u1@test",
                organization_id="org_t",
                role="admin",  # not cfo
                exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )

        main.app.dependency_overrides[get_current_user] = _user
        try:
            resp = client.post(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t",
                json={"domain": "acme.com"},
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()

    def test_post_add_rejects_invalid_domain(self, app_client):
        client, main, db = app_client
        _seed_vendor(db)
        self._override_user(main, "cfo")
        try:
            # Whitespace + special chars fail the pydantic regex
            resp = client.post(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t",
                json={"domain": "not valid!"},
            )
            assert resp.status_code == 422
        finally:
            main.app.dependency_overrides.clear()

    def test_delete_happy_path(self, app_client):
        client, main, db = app_client
        _seed_vendor(db, domains=["acme.com"])
        self._override_user(main, "cfo")
        try:
            resp = client.delete(
                "/api/vendors/Acme/trusted-domains/acme.com"
                "?organization_id=org_t"
            )
            assert resp.status_code == 200
            assert resp.json()["trusted_domains"] == []
        finally:
            main.app.dependency_overrides.clear()

    def test_delete_404_when_not_present(self, app_client):
        client, main, db = app_client
        _seed_vendor(db, domains=["acme.com"])
        self._override_user(main, "cfo")
        try:
            resp = client.delete(
                "/api/vendors/Acme/trusted-domains/evil.com"
                "?organization_id=org_t"
            )
            assert resp.status_code == 404
        finally:
            main.app.dependency_overrides.clear()

    def test_cross_tenant_blocked(self, app_client):
        client, main, db = app_client
        _seed_vendor(db, domains=["acme.com"])
        self._override_user(main, "cfo", org_id="other_org")
        try:
            resp = client.get(
                "/api/vendors/Acme/trusted-domains?organization_id=org_t"
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.clear()
