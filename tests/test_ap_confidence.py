"""Tests for the per-field confidence-gate calibration.

Background — the gate was previously a flat 0.95 threshold AND-applied
across vendor, amount, invoice_number, due_date. That sent 100% of
production records into ``field_review_required`` even when the
critical fields extracted correctly: a single weak ``due_date`` score
tanked the whole record. The refactor introduces per-field
calibration with severity tiers — only critical-tier failures gate.

These tests pin both the new semantics and the original profile-
override machinery (which still works on top of the calibration).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from clearledgr.core.ap_confidence import (  # noqa: E402
    DEFAULT_FIELD_CALIBRATION,
    SEVERITY_ADVISORY,
    SEVERITY_CRITICAL,
    SEVERITY_IMPORTANT,
    evaluate_critical_field_confidence,
)


# ---------------------------------------------------------------------------
# Calibration registry contract
# ---------------------------------------------------------------------------


class TestCalibrationRegistry:
    """The registry is the source of truth — its shape is part of the
    public contract that the gate evaluator depends on. Pin it."""

    def test_critical_fields_are_vendor_and_amount(self):
        """Vendor + amount are the only fields whose mis-extraction is a
        financial-integrity failure. Inflating this set triggers the
        false-positive review pattern that prompted the refactor."""
        critical = {
            field for field, calib in DEFAULT_FIELD_CALIBRATION.items()
            if calib.severity == SEVERITY_CRITICAL
        }
        assert critical == {"vendor", "amount"}

    def test_invoice_number_is_important_not_critical(self):
        assert DEFAULT_FIELD_CALIBRATION["invoice_number"].severity == SEVERITY_IMPORTANT

    def test_due_date_is_advisory_not_critical(self):
        """due_date below threshold must not block — operators set
        dates per org policy regardless of invoice text."""
        assert DEFAULT_FIELD_CALIBRATION["due_date"].severity == SEVERITY_ADVISORY

    def test_every_field_carries_a_rationale(self):
        """A future reader must be able to challenge each threshold by
        the reasoning attached, not by guessing why."""
        for field, calib in DEFAULT_FIELD_CALIBRATION.items():
            assert calib.rationale, f"{field} missing rationale"
            assert len(calib.rationale) > 60, (
                f"{field} rationale too thin to defend the threshold"
            )

    def test_thresholds_are_within_unit_interval(self):
        for field, calib in DEFAULT_FIELD_CALIBRATION.items():
            assert 0.0 <= calib.threshold <= 1.0, f"{field} threshold out of range"


# ---------------------------------------------------------------------------
# Severity-tier gate semantics
# ---------------------------------------------------------------------------


class TestSeveritySemantics:
    """Per-field severity decides whether a low-confidence value blocks."""

    def test_critical_field_below_threshold_blocks(self):
        """Vendor at 0.85 (below 0.92 critical threshold) must block."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.90,
            field_values={
                "vendor": "Some Co",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.85,        # below 0.92 critical threshold
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
            vendor_name="Some Co",
            sender="billing@some.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        assert gate["requires_field_review"] is True
        assert [b["field"] for b in gate["confidence_blockers"]] == ["vendor"]
        assert gate["confidence_advisories"] == []

    def test_advisory_field_below_threshold_does_not_block(self):
        """due_date at 0.5 (below 0.6 advisory threshold) attaches an
        advisory but never blocks the gate. This is the regression test
        for the 13/13 production pattern."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.95,
            field_values={
                "vendor": "Some Co",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.99,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.50,      # below 0.60 advisory threshold
            },
            vendor_name="Some Co",
            sender="billing@some.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        assert gate["requires_field_review"] is False
        assert gate["confidence_blockers"] == []
        assert [a["field"] for a in gate["confidence_advisories"]] == ["due_date"]

    def test_important_field_below_threshold_does_not_block_alone(self):
        """invoice_number at 0.70 (below 0.80 important threshold)
        attaches an advisory; doesn't block when criticals are clean."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.90,
            field_values={
                "vendor": "Some Co",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.99,
                "amount": 0.99,
                "invoice_number": 0.70,
                "due_date": 0.99,
            },
            vendor_name="Some Co",
            sender="billing@some.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        assert gate["requires_field_review"] is False
        assert gate["confidence_blockers"] == []
        assert [a["field"] for a in gate["confidence_advisories"]] == ["invoice_number"]

    def test_calibration_decisions_capture_every_field(self):
        """Audit trail: every evaluated field must produce a decision
        record with its full {field, confidence, threshold, severity,
        decision} shape, regardless of pass/fail."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.95,
            field_values={
                "vendor": "Some Co",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.99,
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
            vendor_name="Some Co",
            sender="billing@some.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        decisions = {d["field"]: d for d in gate["calibration_decisions"]}
        assert set(decisions) == {"vendor", "amount", "invoice_number", "due_date"}
        for field, decision in decisions.items():
            assert decision["decision"] == "pass"
            assert decision["severity"] in {SEVERITY_CRITICAL, SEVERITY_IMPORTANT, SEVERITY_ADVISORY}
            assert "confidence" in decision and "threshold" in decision

    def test_field_severities_map_returned_with_result(self):
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.95,
            field_values={"vendor": "X", "amount": 1.0, "invoice_number": "I", "due_date": "2026-02-01"},
            field_confidences={"vendor": 0.99, "amount": 0.99, "invoice_number": 0.99, "due_date": 0.99},
            vendor_name="X", sender="x@y.com", document_type="invoice",
            primary_source="email", has_attachment=False,
        )
        sev = gate["field_severities"]
        assert sev["vendor"] == SEVERITY_CRITICAL
        assert sev["amount"] == SEVERITY_CRITICAL
        assert sev["invoice_number"] == SEVERITY_IMPORTANT
        assert sev["due_date"] == SEVERITY_ADVISORY


# ---------------------------------------------------------------------------
# Regression: the 13/13 production pattern
# ---------------------------------------------------------------------------


class TestProductionPatternRegression:
    """Workspace had 13 unresolved exceptions all in
    ``field_review_required``. With the new calibration, a real-world
    extraction profile (high vendor + amount confidence, soft due_date)
    must no longer trigger the gate."""

    def test_realistic_extraction_with_soft_due_date_passes(self):
        """The pattern that caused the production false-positives:
        Claude returns 0.95+ on vendor/amount/invoice_number but the
        due_date sits at 0.6-0.85 because real invoices are vague
        about payment timing ('Net 30 from issue', 'on receipt')."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.92,
            field_values={
                "vendor": "Acme Ltd",
                "amount": 12_450.00,
                "invoice_number": "INV-2026-104",
                "due_date": "2026-03-01",
            },
            field_confidences={
                "vendor": 0.97,
                "amount": 0.98,
                "invoice_number": 0.93,
                "due_date": 0.78,    # soft — typical real-world extraction
            },
            vendor_name="Acme Ltd",
            sender="billing@acme.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        assert gate["requires_field_review"] is False, (
            "Realistic-but-soft due_date must not block when criticals "
            "are clean — this was the 13/13 production false-positive."
        )
        assert gate["confidence_advisories"] == [], (
            "due_date at 0.78 is above its advisory threshold (0.60); "
            "no advisory should be raised."
        )

    def test_realistic_extraction_with_very_low_due_date_advises_but_passes(self):
        """Even when due_date confidence is low enough to advise, the
        record passes — that's the whole point of the advisory tier."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.92,
            field_values={
                "vendor": "Acme Ltd",
                "amount": 12_450.00,
                "invoice_number": "INV-2026-104",
                "due_date": "2026-03-01",
            },
            field_confidences={
                "vendor": 0.97,
                "amount": 0.98,
                "invoice_number": 0.93,
                "due_date": 0.40,    # well below 0.60 advisory threshold
            },
            vendor_name="Acme Ltd",
            sender="billing@acme.example",
            document_type="invoice",
            primary_source="email",
            has_attachment=False,
        )
        assert gate["requires_field_review"] is False
        assert [a["field"] for a in gate["confidence_advisories"]] == ["due_date"]


# ---------------------------------------------------------------------------
# Profile + override interaction
# ---------------------------------------------------------------------------


class TestProfileMachinery:
    """Profile-based threshold overrides remain functional on top of the
    calibration registry."""

    def test_known_billing_attachment_profile_relaxes_vendor_below_default(self):
        """Stripe / Google Payments invoices clear the gate at 0.91
        vendor confidence (profile override 0.90) even though default
        critical threshold is 0.92."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.91,
            field_values={
                "vendor": "Google Cloud EMEA Limited",
                "amount": 38.46,
                "invoice_number": "5499678906",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.91,
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
        assert gate["field_thresholds"]["vendor"] == 0.9

    def test_critical_field_below_profile_override_still_blocks(self):
        """If a profile tightens vendor to 0.93 and the actual confidence
        is 0.91, the gate must still block — severity stays critical."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.91,
            field_values={
                "vendor": "Some Vendor",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.91,    # below profile threshold 0.93
                "amount": 0.99,
                "invoice_number": 0.99,
                "due_date": 0.99,
            },
            vendor_name="Some Vendor",
            sender="billing@some-vendor.example",
            document_type="invoice",
            primary_source="attachment",
            has_attachment=True,
        )
        assert gate["profile_id"] == "generic_attachment_invoice"
        assert gate["requires_field_review"] is True
        assert [b["field"] for b in gate["confidence_blockers"]] == ["vendor"]

    def test_non_invoice_finance_document_only_evaluates_vendor_amount(self):
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.95,
            field_values={
                "vendor": "Some Co",
                "amount": 100.0,
                "invoice_number": "INV-1",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.99,
                "amount": 0.99,
                "invoice_number": 0.50,
                "due_date": 0.50,
            },
            vendor_name="Some Co",
            sender="billing@some.example",
            document_type="credit_note",
            primary_source="attachment",
            has_attachment=True,
        )
        assert gate["profile_id"] == "non_invoice_finance_document"
        assert set(gate["evaluated_fields"]) == {"vendor", "amount"}
        assert gate["requires_field_review"] is False


# ---------------------------------------------------------------------------
# Default / fallback behaviour
# ---------------------------------------------------------------------------


class TestFallbacks:
    def test_falls_back_to_calibration_when_no_profile_matches(self):
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.97,
            field_values={
                "vendor": "Unknown Vendor",
                "amount": 38.46,
                "invoice_number": "INV-404",
                "due_date": "2026-02-01",
            },
            field_confidences={
                "vendor": 0.98,
                "amount": 0.99,
                "invoice_number": 0.98,
                "due_date": 0.97,
            },
            vendor_name="Unknown Vendor",
            sender="billing@example.com",
            document_type="memo",
            primary_source="portal",
            has_attachment=False,
        )
        assert gate["profile_id"] is None
        assert gate["requires_field_review"] is False
        assert gate["evaluated_fields"] == ["vendor", "amount", "invoice_number", "due_date"]

    def test_missing_field_confidence_uses_overall_as_fallback(self):
        """When the LLM returns no per-field confidence, the gate falls
        back to overall_confidence — preserving the original behaviour
        that the existing intake paths depend on."""
        gate = evaluate_critical_field_confidence(
            overall_confidence=0.85,    # below all critical thresholds
            field_values={"vendor": "X", "amount": 1.0, "invoice_number": "I", "due_date": "2026-02-01"},
            field_confidences=None,
            vendor_name="X", sender="x@y.com", document_type="invoice",
            primary_source="email", has_attachment=False,
        )
        # vendor + amount fall to 0.85 < 0.92 critical → block
        critical_blockers = [b["field"] for b in gate["confidence_blockers"]]
        assert "vendor" in critical_blockers and "amount" in critical_blockers
