from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.ap_confidence import evaluate_critical_field_confidence  # noqa: E402


def test_known_billing_attachment_invoice_profile_reduces_false_positive_field_review():
    gate = evaluate_critical_field_confidence(
        overall_confidence=0.91,
        field_values={
            "vendor": "Google Cloud EMEA Limited",
            "amount": 38.46,
            "invoice_number": "5499678906",
            "due_date": "2026-02-01",
        },
        field_confidences={
            "vendor": 0.94,
            "amount": 0.95,
            "invoice_number": 0.94,
            "due_date": 0.89,
        },
        vendor_name="Google Cloud EMEA Limited",
        sender="Google Payments <payments-noreply@google.com>",
        document_type="invoice",
        primary_source="attachment",
        has_attachment=True,
    )

    assert gate["profile_id"] == "known_billing_attachment_invoice"
    assert gate["requires_field_review"] is False
    assert gate["confidence_blockers"] == []
    assert gate["field_thresholds"]["vendor"] == 0.9
    assert gate["field_thresholds"]["invoice_number"] == 0.9
    assert gate["field_thresholds"]["due_date"] == 0.88


def test_generic_email_invoice_keeps_default_critical_field_threshold():
    gate = evaluate_critical_field_confidence(
        overall_confidence=0.91,
        field_values={
            "vendor": "Google Cloud EMEA Limited",
            "amount": 38.46,
            "invoice_number": "5499678906",
            "due_date": "2026-02-01",
        },
        field_confidences={
            "vendor": 0.94,
            "amount": 0.95,
            "invoice_number": 0.94,
            "due_date": 0.89,
        },
        vendor_name="Google Cloud EMEA Limited",
        sender="billing@example.com",
        document_type="invoice",
        primary_source="email",
        has_attachment=False,
    )

    assert gate["profile_id"] == "generic_email_invoice"
    assert gate["requires_field_review"] is True
    assert [blocker["field"] for blocker in gate["confidence_blockers"]] == [
        "vendor",
        "invoice_number",
        "due_date",
    ]
    assert all(blocker["threshold_pct"] == 95 for blocker in gate["confidence_blockers"])


def test_generic_attachment_invoice_profile_relaxes_but_still_blocks_weak_fields():
    gate = evaluate_critical_field_confidence(
        overall_confidence=0.91,
        field_values={
            "vendor": "Designco",
            "amount": 38.46,
            "invoice_number": "5499678906",
            "due_date": "2026-02-01",
        },
        field_confidences={
            "vendor": 0.94,
            "amount": 0.95,
            "invoice_number": 0.94,
            "due_date": 0.89,
        },
        vendor_name="Designco",
        sender="billing@designco.com",
        document_type="invoice",
        primary_source="attachment",
        has_attachment=True,
    )

    assert gate["profile_id"] == "generic_attachment_invoice"
    assert gate["requires_field_review"] is True
    assert [blocker["field"] for blocker in gate["confidence_blockers"]] == ["due_date"]
    assert gate["field_thresholds"]["due_date"] == 0.91


def test_known_billing_attachment_profile_applies_to_stripe_sender_family():
    gate = evaluate_critical_field_confidence(
        overall_confidence=0.91,
        field_values={
            "vendor": "Stripe, Inc.",
            "amount": 38.46,
            "invoice_number": "5499678906",
            "due_date": "2026-02-01",
        },
        field_confidences={
            "vendor": 0.94,
            "amount": 0.95,
            "invoice_number": 0.94,
            "due_date": 0.89,
        },
        vendor_name="Stripe, Inc.",
        sender="Replit <invoice+statements+acct_15YpNsJAmnYVOvfn@stripe.com>",
        document_type="invoice",
        primary_source="attachment",
        has_attachment=True,
    )

    assert gate["profile_id"] == "known_billing_attachment_invoice"
    assert gate["requires_field_review"] is False
    assert gate["confidence_blockers"] == []
