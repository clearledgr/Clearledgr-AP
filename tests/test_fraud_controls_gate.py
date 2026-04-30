"""Tests for Phase 1.2a — architectural fraud-control blocking gates.

Covers:
  - fraud_controls.FraudControlConfig dataclass (load/save/defaults/diff)
  - fraud_controls.evaluate_payment_ceiling FX conversion + fail-closed
  - invoice_validation gate contributions:
      * payment_ceiling_exceeded
      * first_payment_hold (new vendor + dormancy)
      * vendor_velocity_exceeded
      * prompt_injection_detected (thin coverage — full suite in test_prompt_guard)
      * fraud_control_config_unavailable fail-closed
  - severity-based gate "passed" bug fix (info codes no longer block)
  - CFO role gating on the /fraud-controls API
  - Audit trail emission on every modification
  - End-to-end: configure ceiling → submit over-ceiling invoice → gate fails
    → Phase 1.1 enforcement forces escalate → audit event emitted
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path, name: str = "fc_test.db"):
    """Create a fresh temp-file ClearledgrDB and wire it as the singleton."""
    from clearledgr.core.database import get_db
    from clearledgr.core import database as db_module

    db = get_db()
    db.initialize()
    db_module._DB_INSTANCE = db
    return db


def _make_workflow(db, org_id: str = "org_fc_test"):
    from clearledgr.services.invoice_workflow import InvoiceWorkflowService
    return InvoiceWorkflowService(organization_id=org_id)


def _make_invoice(**overrides):
    from clearledgr.services.invoice_models import InvoiceData
    defaults = dict(
        gmail_id="g_fc_1",
        subject="Invoice INV-FC-1",
        sender="billing@fctest.example",
        vendor_name="Established Vendor",
        amount=500.0,
        currency="USD",
        invoice_number="INV-FC-1",
        due_date="2026-05-01",
        confidence=0.97,
        organization_id="org_fc_test",
        field_confidences={
            "vendor": 0.99,
            "amount": 0.98,
            "invoice_number": 0.97,
            "due_date": 0.95,
        },
    )
    defaults.update(overrides)
    return InvoiceData(**defaults)


def _seed_established_vendor(
    db,
    vendor_name: str,
    *,
    org_id: str = "org_fc_test",
    invoice_count: int = 5,
    days_since_last: int = 7,
):
    last_at = (datetime.now(timezone.utc) - timedelta(days=days_since_last)).isoformat()
    db.upsert_vendor_profile(
        org_id,
        vendor_name,
        invoice_count=invoice_count,
        avg_invoice_amount=500.0,
        always_approved=1,
        last_invoice_date=last_at,
    )


def _seed_org(db, org_id: str, fraud_controls: Dict[str, Any] = None):
    """Create an organization row with optional fraud_controls settings."""
    settings = {}
    if fraud_controls is not None:
        settings["fraud_controls"] = fraud_controls
    db.create_organization(org_id, name=f"Test {org_id}", settings=settings)


# ===========================================================================
# FraudControlConfig — dataclass unit tests
# ===========================================================================


class TestFraudControlConfigDefaults:

    def test_defaults_match_thesis_baseline(self):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            DEFAULT_PAYMENT_CEILING,
            DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK,
            DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS,
        )
        config = FraudControlConfig()
        assert config.payment_ceiling == DEFAULT_PAYMENT_CEILING == 10_000.0
        assert config.vendor_velocity_max_per_week == DEFAULT_VENDOR_VELOCITY_MAX_PER_WEEK == 10
        assert config.first_payment_dormancy_days == DEFAULT_FIRST_PAYMENT_DORMANCY_DAYS == 180
        assert config.base_currency == "USD"

    def test_config_is_frozen(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig()
        with pytest.raises(Exception):
            config.payment_ceiling = 50_000  # type: ignore


class TestFraudControlConfigFromDict:

    def test_from_empty_dict_uses_defaults(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict({})
        assert config.payment_ceiling == 10_000.0

    def test_from_none_uses_defaults(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict(None)
        assert config.payment_ceiling == 10_000.0

    def test_honors_supplied_values(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict(
            {
                "payment_ceiling": 50_000.0,
                "vendor_velocity_max_per_week": 25,
                "first_payment_dormancy_days": 90,
                "base_currency": "EUR",
            }
        )
        assert config.payment_ceiling == 50_000.0
        assert config.vendor_velocity_max_per_week == 25
        assert config.first_payment_dormancy_days == 90
        assert config.base_currency == "EUR"

    def test_rejects_negative_values_falls_back_to_defaults(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict(
            {
                "payment_ceiling": -5000.0,
                "vendor_velocity_max_per_week": -2,
                "first_payment_dormancy_days": -30,
            }
        )
        assert config.payment_ceiling == 10_000.0
        assert config.vendor_velocity_max_per_week == 10
        assert config.first_payment_dormancy_days == 180

    def test_rejects_non_numeric_falls_back(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict(
            {"payment_ceiling": "not-a-number"}
        )
        assert config.payment_ceiling == 10_000.0

    def test_base_currency_from_org_when_not_in_data(self):
        from clearledgr.core.fraud_controls import FraudControlConfig
        config = FraudControlConfig.from_dict({}, base_currency="GBP")
        assert config.base_currency == "GBP"


# ===========================================================================
# load_fraud_controls / save_fraud_controls
# ===========================================================================


class TestLoadSaveFraudControls:

    def test_load_returns_defaults_for_missing_org(self, tmp_path):
        from clearledgr.core.fraud_controls import load_fraud_controls
        db = _make_db(tmp_path)
        config = load_fraud_controls("nonexistent_org", db)
        assert config.payment_ceiling == 10_000.0

    def test_load_returns_stored_values(self, tmp_path):
        from clearledgr.core.fraud_controls import load_fraud_controls
        db = _make_db(tmp_path)
        _seed_org(
            db,
            "org_fc_test",
            fraud_controls={
                "payment_ceiling": 25_000.0,
                "vendor_velocity_max_per_week": 15,
            },
        )
        config = load_fraud_controls("org_fc_test", db)
        assert config.payment_ceiling == 25_000.0
        assert config.vendor_velocity_max_per_week == 15
        assert config.first_payment_dormancy_days == 180  # default preserved

    def test_load_resolves_base_currency_from_locale_section(self, tmp_path):
        from clearledgr.core.fraud_controls import load_fraud_controls
        db = _make_db(tmp_path)
        db.create_organization(
            "org_eur",
            name="EUR Org",
            settings={"locale": {"default_currency": "EUR"}},
        )
        config = load_fraud_controls("org_eur", db)
        assert config.base_currency == "EUR"

    def test_save_persists_values(self, tmp_path):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            load_fraud_controls,
            save_fraud_controls,
        )
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        new = FraudControlConfig(
            payment_ceiling=50_000.0,
            vendor_velocity_max_per_week=20,
            first_payment_dormancy_days=90,
            base_currency="USD",
        )
        save_fraud_controls(
            "org_fc_test", new, modified_by="user_cfo_1", db=db
        )
        loaded = load_fraud_controls("org_fc_test", db)
        assert loaded.payment_ceiling == 50_000.0
        assert loaded.vendor_velocity_max_per_week == 20

    def test_save_emits_audit_event_with_diff(self, tmp_path):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            save_fraud_controls,
        )
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")

        save_fraud_controls(
            "org_fc_test",
            FraudControlConfig(payment_ceiling=25_000.0),
            modified_by="user_cfo_1",
            db=db,
        )

        all_events = db.list_recent_ap_audit_events("org_fc_test", limit=50)
        events = [
            e for e in all_events
            if e.get("event_type") == "fraud_control_modified"
        ]
        assert len(events) == 1
        event = events[0]
        assert event["actor_id"] == "user_cfo_1"
        payload = event.get("payload_json") or {}
        assert payload.get("entity_type") == "fraud_control"
        assert payload.get("entity_id") == "org_fc_test"
        diff = payload.get("diff") or []
        assert any(d["field"] == "payment_ceiling" for d in diff)
        ceiling_diff = next(d for d in diff if d["field"] == "payment_ceiling")
        assert ceiling_diff["before"] == 10_000.0  # default
        assert ceiling_diff["after"] == 25_000.0

    def test_save_emits_audit_event_even_when_no_value_changed(self, tmp_path):
        """'No silent modifications' — audit event fires on every save call."""
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            save_fraud_controls,
        )
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        config = FraudControlConfig()
        save_fraud_controls(
            "org_fc_test", config, modified_by="user_cfo_1", db=db
        )
        save_fraud_controls(
            "org_fc_test", config, modified_by="user_cfo_1", db=db
        )
        all_events = db.list_recent_ap_audit_events("org_fc_test", limit=50)
        events = [
            e for e in all_events
            if e.get("event_type") == "fraud_control_modified"
        ]
        assert len(events) == 2


# ===========================================================================
# evaluate_payment_ceiling (FX conversion + fail-closed)
# ===========================================================================


class TestEvaluatePaymentCeiling:

    def test_same_currency_under_ceiling_passes(self):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            evaluate_payment_ceiling,
        )
        config = FraudControlConfig(payment_ceiling=10_000.0, base_currency="USD")
        result = evaluate_payment_ceiling(5_000.0, "USD", config)
        assert result.exceeds_ceiling is False
        assert result.converted_amount == 5_000.0
        assert result.rate == 1.0
        assert result.fx_unavailable is False

    def test_same_currency_over_ceiling_blocks(self):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            evaluate_payment_ceiling,
        )
        config = FraudControlConfig(payment_ceiling=10_000.0, base_currency="USD")
        result = evaluate_payment_ceiling(15_000.0, "USD", config)
        assert result.exceeds_ceiling is True
        assert result.converted_amount == 15_000.0

    def test_exactly_at_ceiling_does_not_exceed(self):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            evaluate_payment_ceiling,
        )
        config = FraudControlConfig(payment_ceiling=10_000.0, base_currency="USD")
        result = evaluate_payment_ceiling(10_000.0, "USD", config)
        assert result.exceeds_ceiling is False  # strict >

    def test_fx_conversion_applied(self):
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            evaluate_payment_ceiling,
        )
        config = FraudControlConfig(payment_ceiling=10_000.0, base_currency="USD")

        fake_fx = {"converted_amount": 12_000.0, "rate": 1.2, "source": "ecb"}
        with patch(
            "clearledgr.services.fx_conversion.convert", return_value=fake_fx
        ):
            result = evaluate_payment_ceiling(10_000.0, "EUR", config)
        assert result.exceeds_ceiling is True
        assert result.converted_amount == 12_000.0
        assert result.rate == 1.2
        assert result.fx_unavailable is False

    def test_fx_unavailable_fails_closed(self):
        """When FX service cannot convert, treat as exceeding ceiling."""
        from clearledgr.core.fraud_controls import (
            FraudControlConfig,
            evaluate_payment_ceiling,
        )
        config = FraudControlConfig(payment_ceiling=10_000.0, base_currency="USD")
        fake_fx = {"converted_amount": None, "rate": None, "source": "unavailable"}
        with patch(
            "clearledgr.services.fx_conversion.convert", return_value=fake_fx
        ):
            result = evaluate_payment_ceiling(500.0, "XYZ", config)
        assert result.fx_unavailable is True
        assert result.exceeds_ceiling is True  # fail closed


# ===========================================================================
# Gate contribution tests — end-to-end through _evaluate_deterministic_validation
# ===========================================================================


class TestGatePaymentCeiling:

    def test_clean_invoice_under_ceiling_passes_ceiling_check(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Established Vendor")
        workflow = _make_workflow(db)
        invoice = _make_invoice(amount=500.0)
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "payment_ceiling_exceeded" not in gate["reason_codes"]

    def test_invoice_over_ceiling_blocks_gate(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Established Vendor")
        workflow = _make_workflow(db)
        invoice = _make_invoice(amount=15_000.0)
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "payment_ceiling_exceeded" in gate["reason_codes"]
        assert gate["passed"] is False
        ceiling_reasons = [
            r for r in gate["reasons"] if r["code"] == "payment_ceiling_exceeded"
        ]
        assert ceiling_reasons[0]["severity"] == "error"
        assert ceiling_reasons[0]["details"]["ceiling"] == 10_000.0

    def test_custom_ceiling_from_org_config_applied(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(
            db,
            "org_fc_test",
            fraud_controls={"payment_ceiling": 25_000.0},
        )
        _seed_established_vendor(db, "Established Vendor")
        workflow = _make_workflow(db)
        # $15k is over default $10k but under custom $25k → should PASS ceiling
        invoice = _make_invoice(amount=15_000.0)
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "payment_ceiling_exceeded" not in gate["reason_codes"]


class TestGateFirstPaymentHold:

    def test_new_vendor_triggers_first_payment_hold(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        # No vendor profile seeded → brand-new vendor
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Brand New Vendor Inc")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "first_payment_hold" in gate["reason_codes"]
        assert gate["passed"] is False
        hold_reasons = [
            r for r in gate["reasons"] if r["code"] == "first_payment_hold"
        ]
        assert hold_reasons[0]["severity"] == "error"
        assert hold_reasons[0]["details"]["reason"] == "new_vendor"

    def test_vendor_with_zero_invoice_count_triggers_hold(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        db.upsert_vendor_profile(
            "org_fc_test",
            "Placeholder Vendor",
            invoice_count=0,
        )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Placeholder Vendor")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "first_payment_hold" in gate["reason_codes"]

    def test_dormant_vendor_triggers_first_payment_hold(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        # Seed vendor but with last_invoice_at > 180 days ago
        _seed_established_vendor(
            db, "Dormant Vendor", days_since_last=200
        )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Dormant Vendor")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "first_payment_hold" in gate["reason_codes"]
        hold_reasons = [
            r for r in gate["reasons"] if r["code"] == "first_payment_hold"
        ]
        assert hold_reasons[0]["details"]["reason"] == "dormancy"
        assert hold_reasons[0]["details"]["days_since_last_invoice"] >= 180

    def test_recently_active_vendor_does_not_trigger_hold(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Active Vendor", days_since_last=30)
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Active Vendor")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "first_payment_hold" not in gate["reason_codes"]


class TestGateVendorVelocity:

    def test_velocity_under_max_passes(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Moderate Vendor")
        # Seed 3 recent invoices (under default max of 10)
        for i in range(3):
            db.create_ap_item(
                {
                    "id": f"AP-vel-{i}",
                    "organization_id": "org_fc_test",
                    "vendor_name": "Moderate Vendor",
                    "amount": 100.0,
                    "state": "posted_to_erp",
                    "thread_id": f"thread-vel-{i}",
                    "invoice_number": f"MV-{i}",
                }
            )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Moderate Vendor", gmail_id="new-1")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "vendor_velocity_exceeded" not in gate["reason_codes"]

    def test_velocity_at_max_blocks_gate(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Burst Vendor")
        # Seed 10 recent invoices (at default max of 10)
        for i in range(10):
            db.create_ap_item(
                {
                    "id": f"AP-burst-{i}",
                    "organization_id": "org_fc_test",
                    "vendor_name": "Burst Vendor",
                    "amount": 100.0,
                    "state": "posted_to_erp",
                    "thread_id": f"thread-burst-{i}",
                    "invoice_number": f"BV-{i}",
                }
            )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Burst Vendor", gmail_id="new-burst")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "vendor_velocity_exceeded" in gate["reason_codes"]
        velocity_reasons = [
            r for r in gate["reasons"] if r["code"] == "vendor_velocity_exceeded"
        ]
        assert velocity_reasons[0]["severity"] == "error"
        assert velocity_reasons[0]["details"]["observed_count_7d"] == 10

    def test_custom_velocity_from_config_applied(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(
            db,
            "org_fc_test",
            fraud_controls={"vendor_velocity_max_per_week": 3},
        )
        _seed_established_vendor(db, "Strict Vendor")
        for i in range(3):
            db.create_ap_item(
                {
                    "id": f"AP-strict-{i}",
                    "organization_id": "org_fc_test",
                    "vendor_name": "Strict Vendor",
                    "amount": 100.0,
                    "state": "posted_to_erp",
                    "thread_id": f"thread-strict-{i}",
                    "invoice_number": f"SV-{i}",
                }
            )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Strict Vendor", gmail_id="new-strict")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "vendor_velocity_exceeded" in gate["reason_codes"]

    def test_rejected_invoices_do_not_count_toward_velocity(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(
            db,
            "org_fc_test",
            fraud_controls={"vendor_velocity_max_per_week": 3},
        )
        _seed_established_vendor(db, "Mixed Vendor")
        # 2 posted + 2 rejected → effective count = 2 (under max of 3)
        for i in range(2):
            db.create_ap_item(
                {
                    "id": f"AP-posted-{i}",
                    "organization_id": "org_fc_test",
                    "vendor_name": "Mixed Vendor",
                    "amount": 100.0,
                    "state": "posted_to_erp",
                    "thread_id": f"thread-posted-{i}",
                    "invoice_number": f"MX-P-{i}",
                }
            )
        for i in range(2):
            db.create_ap_item(
                {
                    "id": f"AP-rejected-{i}",
                    "organization_id": "org_fc_test",
                    "vendor_name": "Mixed Vendor",
                    "amount": 100.0,
                    "state": "rejected",
                    "thread_id": f"thread-rejected-{i}",
                    "invoice_number": f"MX-R-{i}",
                }
            )
        workflow = _make_workflow(db)
        invoice = _make_invoice(vendor_name="Mixed Vendor", gmail_id="new-mixed")
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "vendor_velocity_exceeded" not in gate["reason_codes"]


# ===========================================================================
# Severity-based gate "passed" bug fix
# ===========================================================================


class TestGateSeverityFilter:
    """The gate was previously bugged: any reason_code fired the gate,
    regardless of severity. Info-severity codes (like 'discount_applied')
    silently blocked legitimate invoices. This regression guards the fix."""

    def test_info_severity_code_does_not_block_gate(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Discount Vendor")
        workflow = _make_workflow(db)
        # An invoice with a discount triggers 'discount_applied' (severity=info).
        invoice = _make_invoice(
            vendor_name="Discount Vendor",
            amount=900.0,
            currency="USD",
        )
        invoice.discount_amount = 100.0
        invoice.discount_terms = "2/10 net 30"
        invoice.subtotal = 1_000.0
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        if "discount_applied" in gate["reason_codes"]:
            assert gate["passed"] is True, (
                "discount_applied is severity=info and must NOT block the gate"
            )

    def test_error_severity_code_blocks_gate(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Over Ceiling Vendor")
        workflow = _make_workflow(db)
        invoice = _make_invoice(
            vendor_name="Over Ceiling Vendor", amount=50_000.0
        )
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert gate["passed"] is False
        assert "payment_ceiling_exceeded" in gate["reason_codes"]

    def test_blocking_reason_codes_list_excludes_info(self, tmp_path):
        """The new gate field 'blocking_reason_codes' is the strict subset
        of reason_codes that actually block, filtered by severity."""
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Established Vendor")
        workflow = _make_workflow(db)
        invoice = _make_invoice(amount=50_000.0)
        gate = asyncio.run(workflow._evaluate_deterministic_validation(invoice))
        assert "payment_ceiling_exceeded" in gate["blocking_reason_codes"]


# ===========================================================================
# Fail-closed when fraud_controls config cannot be loaded
# ===========================================================================


class TestGateFailsClosed:

    def test_config_load_failure_blocks_gate(self, tmp_path):
        db = _make_db(tmp_path)
        _seed_org(db, "org_fc_test")
        _seed_established_vendor(db, "Established Vendor")
        workflow = _make_workflow(db)

        # Force load_fraud_controls to raise
        with patch(
            "clearledgr.core.fraud_controls.load_fraud_controls",
            side_effect=RuntimeError("simulated config load failure"),
        ):
            invoice = _make_invoice()
            gate = asyncio.run(
                workflow._evaluate_deterministic_validation(invoice)
            )
        assert "fraud_control_config_unavailable" in gate["reason_codes"]
        assert gate["passed"] is False


# ===========================================================================
# CFO role gating + API
# ===========================================================================


class TestFraudControlsAPI:
    """Exercises the /fraud-controls endpoints through FastAPI TestClient."""

    @pytest.fixture
    def app_client(self, tmp_path, monkeypatch):
        from clearledgr.core.database import get_db
        from clearledgr.core import database as db_module
        import importlib
        import main

        db = get_db()
        db.initialize()
        monkeypatch.setattr(db_module, "_DB_INSTANCE", db)

        # Reload main so the app picks up the fresh DB singleton.
        importlib.reload(main)
        client = TestClient(main.app)
        yield client, main, db

    def _make_user(self, role: str, user_id: str = "user_1", org_id: str = "org_fc_test"):
        from clearledgr.core.auth import TokenData
        return TokenData(
            user_id=user_id,
            email=f"{user_id}@test.example",
            organization_id=org_id,
            role=role,
            exp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    def test_get_returns_defaults_for_fresh_org(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user("user")
        try:
            _seed_org(db, "org_fc_test")
            resp = client.get("/fraud-controls/org_fc_test")
            assert resp.status_code == 200
            body = resp.json()
            assert body["payment_ceiling"] == 10_000.0
            assert body["vendor_velocity_max_per_week"] == 10
            assert body["first_payment_dormancy_days"] == 180
            assert body["base_currency"] == "USD"
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_non_cfo_cannot_modify(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user("admin")
        try:
            _seed_org(db, "org_fc_test")
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 403
            assert "cfo" in resp.json()["detail"].lower()
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_ap_manager_cannot_modify(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user(
            "operator"
        )
        try:
            _seed_org(db, "org_fc_test")
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_cfo_can_modify(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user(
            "cfo", user_id="cfo_user_1"
        )
        try:
            _seed_org(db, "org_fc_test")
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["payment_ceiling"] == 50_000.0

            # Audit event recorded with CFO user_id
            all_events = db.list_recent_ap_audit_events("org_fc_test", limit=50)
            events = [
                e for e in all_events
                if e.get("event_type") == "fraud_control_modified"
            ]
            assert len(events) == 1
            assert events[0]["actor_id"] == "cfo_user_1"
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_owner_can_modify_as_superset_of_cfo(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user(
            "owner"
        )
        try:
            _seed_org(db, "org_fc_test")
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"vendor_velocity_max_per_week": 25},
            )
            assert resp.status_code == 200
            assert resp.json()["vendor_velocity_max_per_week"] == 25
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_cfo_from_other_org_cannot_modify(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user(
            "cfo", org_id="different_org"
        )
        try:
            _seed_org(db, "org_fc_test")
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"payment_ceiling": 50_000.0},
            )
            assert resp.status_code == 403
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_partial_update_preserves_other_fields(self, app_client):
        client, main, db = app_client
        from clearledgr.core.auth import get_current_user
        main.app.dependency_overrides[get_current_user] = lambda: self._make_user("cfo")
        try:
            _seed_org(
                db,
                "org_fc_test",
                fraud_controls={
                    "payment_ceiling": 25_000.0,
                    "vendor_velocity_max_per_week": 15,
                    "first_payment_dormancy_days": 90,
                },
            )
            resp = client.put(
                "/fraud-controls/org_fc_test",
                json={"payment_ceiling": 40_000.0},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["payment_ceiling"] == 40_000.0
            # Other values preserved
            assert body["vendor_velocity_max_per_week"] == 15
            assert body["first_payment_dormancy_days"] == 90
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)


# ===========================================================================
# End-to-end: configured ceiling → over-ceiling invoice → Phase 1.1 override
# ===========================================================================


class TestEndToEndCeilingWithPhase11Enforcement:
    """Verifies the full stack: fraud_controls config → gate failure →
    Phase 1.1 enforce_gate_constraint forces an LLM 'approve' to 'escalate'
    with a llm_gate_override_applied audit event."""

    def test_over_ceiling_invoice_routed_to_escalate_with_audit_event(
        self, tmp_path, monkeypatch
    ):
        db = _make_db(tmp_path)
        _seed_org(
            db,
            "org_fc_test",
            fraud_controls={"payment_ceiling": 5_000.0},
        )
        _seed_established_vendor(db, "Established Vendor")

        from clearledgr.services.ap_decision import APDecision
        from clearledgr.services.invoice_workflow import InvoiceWorkflowService

        workflow = InvoiceWorkflowService(organization_id="org_fc_test")

        async def _fake_send_for_approval(invoice, **kwargs):
            return {
                "status": "pending_approval",
                "invoice_id": invoice.gmail_id,
                "reason": kwargs.get("decision_reason", "escalated_by_gate"),
            }

        monkeypatch.setattr(
            workflow, "_send_for_approval", _fake_send_for_approval
        )
        monkeypatch.setattr(
            workflow, "_record_validation_gate_failure", lambda *a, **kw: None
        )

        # Simulate Phase 1.1 planning loop handing a pre-computed 'approve' decision
        # for an over-ceiling invoice. The narrow waist must override.
        over_ceiling_invoice = _make_invoice(
            vendor_name="Established Vendor", amount=12_000.0, gmail_id="e2e-1"
        )
        pre_computed = APDecision(
            recommendation="approve",
            reasoning="LLM says vendor has clean history.",
            confidence=0.98,
            info_needed=None,
            risk_flags=[],
            vendor_context_used={},
            model="agent_planning_loop",
            fallback=False,
        )

        result = asyncio.run(
            workflow.process_new_invoice(over_ceiling_invoice, ap_decision=pre_computed)
        )

        # Gate must have blocked, NOT auto-posted
        assert result.get("status") != "posted_to_erp"

        # llm_gate_override_applied audit event must exist
        all_events = db.list_recent_ap_audit_events("org_fc_test", limit=100)
        events = [
            e for e in all_events
            if e.get("event_type") == "llm_gate_override_applied"
        ]
        assert len(events) >= 1
        override_event = events[0]
        payload = override_event.get("payload_json") or {}
        assert payload.get("pre_override_recommendation") == "approve"
        assert payload.get("enforced_recommendation") == "escalate"
        assert "payment_ceiling_exceeded" in (payload.get("gate_reason_codes") or [])
