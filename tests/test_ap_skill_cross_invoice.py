"""Tests for cross-invoice memory in AP skill enrichment and system prompt.

Validates that:
- _handle_enrich_with_context integrates CrossInvoiceAnalyzer results
- Graceful fallback when cross-invoice analysis fails
- build_system_prompt includes duplicate/anomaly warnings when issues exist
- build_system_prompt omits warnings when analysis is clean
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from clearledgr.core.skills.ap_skill import APSkill, _handle_enrich_with_context
from clearledgr.core.skills.base import AgentTask
from clearledgr.services.cross_invoice_analysis import (
    AnomalyAlert,
    CrossInvoiceAnalysis,
    DuplicateAlert,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _invoice_payload():
    return {
        "gmail_id": "msg-abc-123",
        "subject": "Invoice INV-9001 from Acme Corp",
        "sender": "billing@acme.com",
        "vendor_name": "Acme Corp",
        "amount": 1500.00,
        "currency": "USD",
        "confidence": 0.92,
        "invoice_number": "INV-9001",
        "due_date": "2026-04-15",
    }


def _analysis_with_issues():
    return CrossInvoiceAnalysis(
        has_issues=True,
        duplicates=[
            DuplicateAlert(
                severity="high",
                message="Potential duplicate: Same invoice number, Same amount ($1,500.00)",
                matching_invoice_id="msg-old-777",
                match_score=0.85,
                details={"reasons": ["Same invoice number", "Same amount"]},
            ),
        ],
        anomalies=[
            AnomalyAlert(
                severity="warning",
                anomaly_type="amount",
                message="Amount $1,500.00 is 60% higher than typical $937.50",
                expected_value=937.50,
                actual_value=1500.00,
                deviation_pct=60.0,
            ),
        ],
        vendor_stats={"invoice_count": 5},
        recommendations=["Review for potential duplicate payment"],
    )


def _clean_analysis():
    return CrossInvoiceAnalysis(
        has_issues=False,
        duplicates=[],
        anomalies=[],
        vendor_stats={"invoice_count": 3},
        recommendations=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_enrich_includes_cross_invoice_data():
    """_handle_enrich_with_context populates cross_invoice_analysis from analyzer."""
    analysis = _analysis_with_issues()

    mock_db = MagicMock()
    mock_db.get_vendor_profile.return_value = {}
    mock_db.get_vendor_invoice_history.return_value = []
    mock_db.get_vendor_decision_feedback.return_value = []

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = analysis

    mock_correction_svc = MagicMock()
    mock_correction_svc.suggest.return_value = []

    mock_invoice = MagicMock()
    mock_invoice.vendor_name = "Acme Corp"
    mock_invoice.amount = 1500.00
    mock_invoice.invoice_number = "INV-9001"
    mock_invoice.due_date = "2026-04-15"
    mock_invoice.currency = "USD"
    mock_invoice.gmail_id = "msg-abc-123"
    mock_invoice.invoice_text = ""

    with (
        patch("clearledgr.core.skills.ap_skill._build_invoice", return_value=mock_invoice),
        patch("clearledgr.core.database.get_db", return_value=mock_db),
        patch(
            "clearledgr.services.correction_learning.get_correction_learning_service",
            return_value=mock_correction_svc,
        ),
        patch(
            "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
            return_value=mock_analyzer,
        ),
    ):
        result = asyncio.run(
            _handle_enrich_with_context(
                invoice_payload=_invoice_payload(),
                organization_id="org-1",
            )
        )

    assert result["ok"] is True
    ci = result["cross_invoice_analysis"]
    assert ci["has_issues"] is True
    assert len(ci["duplicates"]) == 1
    assert ci["duplicates"][0]["match_score"] == 0.85
    assert len(ci["anomalies"]) == 1


def test_enrich_cross_invoice_failure_graceful():
    """If the cross-invoice analyzer raises, enrichment still succeeds with empty dict."""
    mock_db = MagicMock()
    mock_db.get_vendor_profile.return_value = {}
    mock_db.get_vendor_invoice_history.return_value = []
    mock_db.get_vendor_decision_feedback.return_value = []

    mock_correction_svc = MagicMock()
    mock_correction_svc.suggest.return_value = []

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.side_effect = RuntimeError("DB connection lost")

    mock_invoice = MagicMock()
    mock_invoice.vendor_name = "Acme Corp"
    mock_invoice.amount = 1500.00
    mock_invoice.invoice_number = "INV-9001"
    mock_invoice.due_date = "2026-04-15"
    mock_invoice.currency = "USD"
    mock_invoice.gmail_id = "msg-abc-123"
    mock_invoice.invoice_text = ""

    with (
        patch("clearledgr.core.skills.ap_skill._build_invoice", return_value=mock_invoice),
        patch("clearledgr.core.database.get_db", return_value=mock_db),
        patch(
            "clearledgr.services.correction_learning.get_correction_learning_service",
            return_value=mock_correction_svc,
        ),
        patch(
            "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
            return_value=mock_analyzer,
        ),
    ):
        result = asyncio.run(
            _handle_enrich_with_context(
                invoice_payload=_invoice_payload(),
                organization_id="org-1",
            )
        )

    assert result["ok"] is True
    assert result["cross_invoice_analysis"] == {}


def test_system_prompt_includes_duplicate_warning():
    """build_system_prompt appends DUPLICATE RISK and ANOMALY lines when has_issues is True."""
    analysis = _analysis_with_issues()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = analysis

    task = AgentTask(
        task_type="ap_invoice_processing",
        organization_id="org-1",
        payload={
            "invoice": {
                "vendor_name": "Acme Corp",
                "amount": 1500.00,
                "currency": "USD",
                "confidence": 0.92,
                "invoice_number": "INV-9001",
                "gmail_id": "msg-abc-123",
            }
        },
    )

    with patch(
        "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
        return_value=mock_analyzer,
    ):
        prompt = APSkill("org-1").build_system_prompt(task)

    assert "Cross-invoice warnings:" in prompt
    assert "DUPLICATE RISK" in prompt
    assert "ANOMALY" in prompt
    assert "Consider escalating" in prompt


def test_system_prompt_no_warning_when_clean():
    """build_system_prompt omits cross-invoice warnings when has_issues is False."""
    analysis = _clean_analysis()
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = analysis

    task = AgentTask(
        task_type="ap_invoice_processing",
        organization_id="org-1",
        payload={
            "invoice": {
                "vendor_name": "Acme Corp",
                "amount": 500.00,
                "currency": "USD",
                "confidence": 0.97,
            }
        },
    )

    with patch(
        "clearledgr.services.cross_invoice_analysis.get_cross_invoice_analyzer",
        return_value=mock_analyzer,
    ):
        prompt = APSkill("org-1").build_system_prompt(task)

    assert "Cross-invoice warnings" not in prompt
    assert "DUPLICATE RISK" not in prompt
