"""Tests for P2 #15 — AP decision override captures the human's reason.

When an AP Manager disagrees with Claude's routing recommendation
(approves something Claude said escalate, rejects something Claude
said approve), the ``ap_decision_override`` audit event now carries
the human's free-text justification in its metadata. CS dashboards
read this to answer "why did you override?" without digging into
raw ap_items rows.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from clearledgr.services.invoice_validation import InvoiceValidationMixin


def _make_service(item_metadata: Dict[str, Any]) -> InvoiceValidationMixin:
    """Build an InvoiceValidationMixin with a stubbed DB that returns
    ``item_metadata`` when asked for an AP item, and captures every
    appended audit event for inspection.
    """
    svc = InvoiceValidationMixin.__new__(InvoiceValidationMixin)
    svc.organization_id = "test-org"

    captured_events: list = []

    db = MagicMock()
    db.get_ap_item.return_value = {"id": "ap-1", "metadata": json.dumps(item_metadata)}
    db.append_audit_event.side_effect = lambda evt: captured_events.append(evt)

    svc.db = db
    svc._captured_events = captured_events  # type: ignore[attr-defined]
    return svc


class TestOverrideReasoning:

    def test_rejection_reason_lands_in_event_metadata(self):
        # Claude said approve. Human rejected. That's an override.
        svc = _make_service({"ap_decision_recommendation": "approve"})

        svc._maybe_record_ap_decision_override(
            ap_item_id="ap-1",
            human_action="rejected",
            actor_id="alice@co",
            human_reason="Vendor not onboarded yet — hold until KYC clears",
        )

        events = svc._captured_events  # type: ignore[attr-defined]
        assert len(events) == 1
        meta = events[0]["metadata"]
        assert meta["human_action"] == "rejected"
        assert meta["claude_recommendation"] == "approve"
        assert meta["human_reason"].startswith("Vendor not onboarded")

    def test_approval_override_context_scalars_captured(self):
        # Claude said escalate. Human approved with a PO-override
        # justification + structured override context.
        svc = _make_service({"ap_decision_recommendation": "escalate"})

        svc._maybe_record_ap_decision_override(
            ap_item_id="ap-1",
            human_action="approved",
            actor_id="cfo@co",
            human_reason="po_override: invoice matches urgent PO-1234 | justification: approved by CFO",
            override_context={
                "gate_type": "po_exception",
                "reason_code": "po_missing_tolerance_exception",
                "confidence_pct": 88,
                "amount_delta_pct": 1.5,
                "unrelated_field": "should_not_land",
            },
        )

        events = svc._captured_events  # type: ignore[attr-defined]
        assert len(events) == 1
        meta = events[0]["metadata"]
        assert meta["human_action"] == "approved"
        assert meta["claude_recommendation"] == "escalate"
        assert meta["gate_type"] == "po_exception"
        assert meta["reason_code"] == "po_missing_tolerance_exception"
        assert meta["confidence_pct"] == 88
        assert meta["amount_delta_pct"] == 1.5
        # Only whitelisted scalar fields land; arbitrary context keys
        # don't leak into the audit event payload.
        assert "unrelated_field" not in meta

    def test_long_reason_is_truncated_to_500_chars(self):
        svc = _make_service({"ap_decision_recommendation": "approve"})
        long_reason = "x" * 1000
        svc._maybe_record_ap_decision_override(
            ap_item_id="ap-1",
            human_action="rejected",
            actor_id="ops@co",
            human_reason=long_reason,
        )
        events = svc._captured_events  # type: ignore[attr-defined]
        assert len(events[0]["metadata"]["human_reason"]) == 500

    def test_no_reason_still_records_override(self):
        """Missing reason shouldn't block the event — structured
        fields (human_action, claude_recommendation) are still useful
        on their own."""
        svc = _make_service({"ap_decision_recommendation": "approve"})
        svc._maybe_record_ap_decision_override(
            ap_item_id="ap-1",
            human_action="rejected",
            actor_id="ops@co",
        )
        events = svc._captured_events  # type: ignore[attr-defined]
        assert len(events) == 1
        assert "human_reason" not in events[0]["metadata"]

    def test_agreement_is_not_recorded_as_override(self):
        """Human agreeing with Claude is not an override — no event."""
        svc = _make_service({"ap_decision_recommendation": "approve"})
        svc._maybe_record_ap_decision_override(
            ap_item_id="ap-1",
            human_action="approved",  # agrees with Claude
            actor_id="ops@co",
            human_reason="LGTM",
        )
        events = svc._captured_events  # type: ignore[attr-defined]
        assert events == []
