"""Tests for the AP reasoning layer (APDecisionService + VendorStore).

All tests use a temp-file SQLite DB — no :memory: (connections don't share state).
Claude API calls are monkeypatched so tests run offline.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path) -> Any:
    """Create and initialise a real ClearledgrDB backed by a temp file."""
    db_file = str(tmp_path / "test_ap_decision.db")
    from clearledgr.core.database import ClearledgrDB
    db = ClearledgrDB(db_path=db_file)
    db.initialize()
    return db


def _make_invoice(**kwargs) -> Any:
    """Build a minimal InvoiceData for tests."""
    from clearledgr.services.invoice_workflow import InvoiceData
    defaults = dict(
        gmail_id="gmail_test_001",
        subject="Invoice INV-001 from Test Vendor",
        sender="billing@testvendor.com",
        vendor_name="Test Vendor Inc",
        amount=2500.00,
        currency="USD",
        invoice_number="INV-001",
        due_date="2026-03-15",
        confidence=0.97,
        organization_id="org_test",
        field_confidences={
            "vendor": 0.99,
            "amount": 0.97,
            "invoice_number": 0.95,
            "due_date": 0.92,
        },
    )
    defaults.update(kwargs)
    return InvoiceData(**defaults)


def _fake_claude_response(recommendation: str, reasoning: str, info_needed=None, risk_flags=None) -> Dict:
    """Return a fake Anthropic API response dict."""
    payload = {
        "recommendation": recommendation,
        "reasoning": reasoning,
        "confidence": 0.92,
        "info_needed": info_needed,
        "risk_flags": risk_flags or [],
    }
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "model": "claude-sonnet-4-6",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAPDecisionService:

    def test_approve_known_vendor_matching_pattern(self, tmp_path, monkeypatch):
        """Vendor with 6 clean invoices in the right amount range → approve."""
        from clearledgr.services.ap_decision import APDecisionService

        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Test Vendor Inc"

        # Seed vendor profile + 6 clean history rows
        db.upsert_vendor_profile(org_id, vendor,
            invoice_count=6, avg_invoice_amount=2400.0, amount_stddev=150.0,
            always_approved=1, requires_po=0)
        for i in range(6):
            db.record_vendor_invoice(
                org_id, vendor, f"AP-hist-{i}",
                amount=2400.0 + i * 20, final_state="posted_to_erp",
                was_approved=True, invoice_date=f"2025-{10+i:02d}-01",
            )

        invoice = _make_invoice()
        vendor_profile = db.get_vendor_profile(org_id, vendor)
        vendor_history = db.get_vendor_invoice_history(org_id, vendor, limit=6)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        # Patch the HTTP call
        with patch.object(APDecisionService, "_call_claude",
                          new_callable=AsyncMock,
                          return_value=_fake_claude_response(
                              "approve",
                              "Test Vendor Inc has 6 clean invoices at avg $2,400. "
                              "Current amount $2,500 is within 2σ. No anomalies. Approving.",
                          )):
            svc = APDecisionService(api_key="test-key")
            decision = asyncio.run(svc.decide(
                invoice,
                vendor_profile=vendor_profile,
                vendor_history=vendor_history,
                validation_gate={"passed": True, "reason_codes": []},
            ))

        assert decision.recommendation == "approve"
        assert decision.fallback is False
        assert "Test Vendor" in decision.reasoning or "invoice" in decision.reasoning.lower()
        assert decision.risk_flags == []

    def test_risk_flag_bank_details_changed(self, tmp_path, monkeypatch):
        """Bank details changed within 30 days → risk_flags contains bank signal."""
        from clearledgr.services.ap_decision import APDecisionService

        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Risky Vendor Ltd"

        recent_change = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        db.upsert_vendor_profile(org_id, vendor,
            invoice_count=4, avg_invoice_amount=1000.0,
            bank_details_changed_at=recent_change, always_approved=0)

        invoice = _make_invoice(vendor_name=vendor, amount=1000.0)
        vendor_profile = db.get_vendor_profile(org_id, vendor)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch.object(APDecisionService, "_call_claude",
                          new_callable=AsyncMock,
                          return_value=_fake_claude_response(
                              "escalate",
                              "Bank details changed 5 days ago — flagging for human review.",
                              risk_flags=["bank_details_changed"],
                          )):
            svc = APDecisionService(api_key="test-key")
            decision = asyncio.run(svc.decide(
                invoice,
                vendor_profile=vendor_profile,
                vendor_history=[],
                validation_gate={"passed": True, "reason_codes": []},
            ))

        assert decision.recommendation == "escalate"
        assert "bank_details_changed" in decision.risk_flags

    def test_needs_info_missing_po_required(self, tmp_path, monkeypatch):
        """PO required by vendor profile but missing → needs_info with a question."""
        from clearledgr.services.ap_decision import APDecisionService

        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "PO Vendor Corp"

        db.upsert_vendor_profile(org_id, vendor,
            invoice_count=3, avg_invoice_amount=5000.0, requires_po=1)

        invoice = _make_invoice(vendor_name=vendor, amount=5000.0, po_number=None)
        vendor_profile = db.get_vendor_profile(org_id, vendor)

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        with patch.object(APDecisionService, "_call_claude",
                          new_callable=AsyncMock,
                          return_value=_fake_claude_response(
                              "needs_info",
                              "PO reference is required for PO Vendor Corp but was not found.",
                              info_needed="Please provide the PO number for invoice INV-001.",
                          )):
            svc = APDecisionService(api_key="test-key")
            decision = asyncio.run(svc.decide(
                invoice,
                vendor_profile=vendor_profile,
                vendor_history=[],
                validation_gate={"passed": True, "reason_codes": ["po_required_missing"]},
            ))

        assert decision.recommendation == "needs_info"
        assert decision.info_needed is not None
        assert len(decision.info_needed) > 10  # has a real question

    def test_fallback_no_api_key(self, tmp_path, monkeypatch):
        """No API key → fallback=True, APDecision still valid and derived from gate."""
        from clearledgr.services.ap_decision import APDecisionService

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        invoice = _make_invoice(confidence=0.97)
        svc = APDecisionService(api_key=None)
        decision = asyncio.run(svc.decide(
            invoice,
            validation_gate={"passed": True, "reason_codes": []},
        ))

        assert decision.fallback is True
        assert decision.recommendation in {"approve", "needs_info", "escalate", "reject"}
        assert decision.model == "fallback"
        # High-confidence invoice with passing gate should recommend approve
        assert decision.recommendation == "approve"

    def test_fallback_low_confidence(self, tmp_path, monkeypatch):
        """No API key, low confidence → fallback escalates to human."""
        from clearledgr.services.ap_decision import APDecisionService

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        invoice = _make_invoice(confidence=0.72)
        svc = APDecisionService(api_key=None)
        decision = asyncio.run(svc.decide(
            invoice,
            validation_gate={"passed": True, "reason_codes": []},
        ))

        assert decision.fallback is True
        assert decision.recommendation == "escalate"
        assert "low_extraction_confidence" in decision.risk_flags

    def test_fallback_respects_strict_human_feedback_bias(self, monkeypatch):
        """Strict vendor feedback should make fallback more conservative."""
        from clearledgr.services.ap_decision import APDecisionService

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        invoice = _make_invoice(confidence=0.97, vendor_name="Feedback Vendor")
        svc = APDecisionService(api_key=None)

        decision = asyncio.run(svc.decide(
            invoice,
            validation_gate={"passed": True, "reason_codes": []},
            decision_feedback={
                "total_feedback": 6,
                "strictness_bias": "strict",
                "override_rate": 0.5,
            },
        ))

        assert decision.fallback is True
        assert decision.recommendation == "escalate"
        assert "human_feedback_strict_bias" in decision.risk_flags


class TestVendorStore:

    def test_vendor_profile_updated_after_outcome(self, tmp_path):
        """update_vendor_profile_from_outcome → invoice_count+1, avg updated."""
        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Learning Vendor SA"

        # Seed initial profile
        db.upsert_vendor_profile(org_id, vendor, invoice_count=0)

        # First outcome
        db.update_vendor_profile_from_outcome(
            org_id, vendor,
            ap_item_id="AP-001",
            final_state="posted_to_erp",
            was_approved=True,
            amount=1000.0,
            invoice_date="2026-01-15",
        )

        profile = db.get_vendor_profile(org_id, vendor)
        assert profile is not None
        assert profile["invoice_count"] == 1
        assert profile["avg_invoice_amount"] == pytest.approx(1000.0)
        assert profile["always_approved"] == 0  # need >= 3 for always_approved

        # Add more outcomes to trigger always_approved
        for i in range(2):
            db.update_vendor_profile_from_outcome(
                org_id, vendor,
                ap_item_id=f"AP-00{i+2}",
                final_state="posted_to_erp",
                was_approved=True,
                amount=1000.0 + i * 100,
            )

        profile = db.get_vendor_profile(org_id, vendor)
        assert profile["invoice_count"] == 3
        assert profile["always_approved"] == 1  # all 3 approved
        # amounts: 1000, 1000, 1100 → avg = 1033.33
        assert profile["avg_invoice_amount"] == pytest.approx(1033.33, rel=0.01)

    def test_get_vendor_invoice_history_respects_limit(self, tmp_path):
        """get_vendor_invoice_history(limit=3) returns at most 3 rows."""
        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Limit Test Vendor"

        for i in range(7):
            db.record_vendor_invoice(
                org_id, vendor, f"AP-lim-{i}",
                amount=float(100 + i), final_state="posted_to_erp", was_approved=True,
            )

        history = db.get_vendor_invoice_history(org_id, vendor, limit=3)
        assert len(history) == 3

    def test_upsert_creates_then_updates(self, tmp_path):
        """Upsert creates a new profile then updates it without duplicating."""
        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Upsert Vendor"

        db.upsert_vendor_profile(org_id, vendor, invoice_count=1)
        p1 = db.get_vendor_profile(org_id, vendor)
        assert p1 is not None
        assert p1["invoice_count"] == 1

        db.upsert_vendor_profile(org_id, vendor, invoice_count=5, typical_gl_code="6100")
        p2 = db.get_vendor_profile(org_id, vendor)
        assert p2["invoice_count"] == 5
        assert p2["typical_gl_code"] == "6100"
        assert p2["id"] == p1["id"]  # same row, not a duplicate

    def test_vendor_decision_feedback_summary_tracks_overrides_and_request_info(self, tmp_path):
        db = _make_db(tmp_path)
        org_id = "org_test"
        vendor = "Feedback Vendor"

        db.record_vendor_decision_feedback(
            org_id,
            vendor,
            ap_item_id="AP-1",
            human_decision="reject",
            agent_recommendation="approve",
            decision_override=True,
            reason="duplicate_invoice",
            action_outcome="rejected",
        )
        db.record_vendor_decision_feedback(
            org_id,
            vendor,
            ap_item_id="AP-2",
            human_decision="request_info",
            agent_recommendation="approve",
            decision_override=True,
            reason="missing_po",
            action_outcome="needs_info",
        )
        db.record_vendor_decision_feedback(
            org_id,
            vendor,
            ap_item_id="AP-3",
            human_decision="approve",
            agent_recommendation="escalate",
            decision_override=True,
            reason="manual_override_with_context",
            action_outcome="posted_to_erp",
        )

        summary = db.get_vendor_decision_feedback_summary(org_id, vendor)
        assert summary["total_feedback"] == 3
        assert summary["reject_count"] == 1
        assert summary["request_info_count"] == 1
        assert summary["approve_count"] == 1
        assert summary["override_count"] == 3
        assert summary["reject_after_approve_count"] == 1
        assert summary["request_info_after_approve_count"] == 1
        assert summary["strictness_bias"] == "strict"
