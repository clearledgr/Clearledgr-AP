"""Tests for Gmail label taxonomy — thesis §6.4 three-level hierarchy.

Covers:
  - CLEARLEDGR_LABELS maps to thesis-defined 3-level nested names
  - _resolve_label_key maps old flat keys to new thesis keys
  - finance_label_keys returns thesis-correct keys for invoices,
    credit notes, statements, exceptions, review-required items
  - AP_STATE_TO_LABEL maps every AP state to the correct thesis label
  - cleanup_legacy_labels migrates flat labels to nested hierarchy
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from clearledgr.services.gmail_labels import (
    CLEARLEDGR_LABELS,
    AP_STATE_TO_LABEL,
    _resolve_label_key,
    cleanup_legacy_labels,
    finance_label_keys,
)


# ===========================================================================
# Label hierarchy matches thesis §6.4
# ===========================================================================


class TestLabelHierarchy:

    def test_thesis_invoice_labels_present(self):
        assert CLEARLEDGR_LABELS["invoice_received"] == "Clearledgr/Invoice/Received"
        assert CLEARLEDGR_LABELS["invoice_matched"] == "Clearledgr/Invoice/Matched"
        assert CLEARLEDGR_LABELS["invoice_exception"] == "Clearledgr/Invoice/Exception"
        assert CLEARLEDGR_LABELS["invoice_approved"] == "Clearledgr/Invoice/Approved"
        assert CLEARLEDGR_LABELS["invoice_paid"] == "Clearledgr/Invoice/Paid"

    def test_thesis_vendor_label_present(self):
        assert CLEARLEDGR_LABELS["vendor_onboarding"] == "Clearledgr/Vendor/Onboarding"

    def test_thesis_finance_labels_present(self):
        assert CLEARLEDGR_LABELS["finance_credit_note"] == "Clearledgr/Finance/Credit Note"
        assert CLEARLEDGR_LABELS["finance_statement"] == "Clearledgr/Finance/Statement"
        assert CLEARLEDGR_LABELS["finance_query"] == "Clearledgr/Finance/Query"
        assert CLEARLEDGR_LABELS["finance_renewal"] == "Clearledgr/Finance/Renewal"

    def test_thesis_classification_labels_present(self):
        assert CLEARLEDGR_LABELS["review_required"] == "Clearledgr/Review Required"
        assert CLEARLEDGR_LABELS["not_finance"] == "Clearledgr/Not Finance"

    def test_all_labels_are_three_level_or_two_level(self):
        for key, name in CLEARLEDGR_LABELS.items():
            parts = name.split("/")
            assert parts[0] == "Clearledgr", f"{key}: {name} doesn't start with Clearledgr/"
            assert len(parts) >= 2, f"{key}: {name} is not nested under Clearledgr/"


# ===========================================================================
# Key alias resolution (backward compatibility)
# ===========================================================================


class TestKeyAliasResolution:

    def test_old_flat_keys_resolve_to_new(self):
        assert _resolve_label_key("invoices") == "invoice_received"
        assert _resolve_label_key("processed") == "invoice_received"
        assert _resolve_label_key("needs_approval") == "invoice_matched"
        assert _resolve_label_key("exceptions") == "invoice_exception"
        assert _resolve_label_key("approved") == "invoice_approved"
        assert _resolve_label_key("posted") == "invoice_paid"
        assert _resolve_label_key("needs_review") == "review_required"
        assert _resolve_label_key("credit_notes") == "finance_credit_note"
        assert _resolve_label_key("bank_statements") == "finance_statement"

    def test_new_keys_pass_through(self):
        assert _resolve_label_key("invoice_received") == "invoice_received"
        assert _resolve_label_key("vendor_onboarding") == "vendor_onboarding"
        assert _resolve_label_key("not_finance") == "not_finance"

    def test_unknown_key_passes_through(self):
        assert _resolve_label_key("completely_unknown") == "completely_unknown"


# ===========================================================================
# AP_STATE_TO_LABEL — every AP state maps to a thesis label
# ===========================================================================


class TestApStateMapping:

    def test_all_ap_states_mapped(self):
        expected_states = {
            "received", "validated", "needs_info",
            "needs_approval", "pending_approval",
            "approved", "ready_to_post",
            "posted_to_erp", "closed",
            "reversed", "failed_post", "rejected",
        }
        assert set(AP_STATE_TO_LABEL.keys()) == expected_states

    def test_happy_path_states(self):
        assert AP_STATE_TO_LABEL["received"] == "invoice_received"
        assert AP_STATE_TO_LABEL["needs_approval"] == "invoice_matched"
        assert AP_STATE_TO_LABEL["approved"] == "invoice_approved"
        assert AP_STATE_TO_LABEL["posted_to_erp"] == "invoice_paid"

    def test_exception_states(self):
        assert AP_STATE_TO_LABEL["needs_info"] == "invoice_exception"
        assert AP_STATE_TO_LABEL["failed_post"] == "invoice_exception"
        assert AP_STATE_TO_LABEL["rejected"] == "invoice_exception"
        assert AP_STATE_TO_LABEL["reversed"] == "invoice_exception"


# ===========================================================================
# finance_label_keys
# ===========================================================================


class TestFinanceLabelKeys:

    def test_blocked_invoice_review(self):
        ap_item = {
            "state": "needs_approval",
            "requires_field_review": True,
            "exception_code": "field_conflict",
            "metadata": {
                "email_type": "invoice",
                "source_conflicts": [
                    {"field": "amount", "blocking": True},
                ],
            },
        }
        keys = finance_label_keys(ap_item=ap_item)
        assert "invoice_received" in keys
        assert "invoice_matched" in keys
        assert "review_required" in keys
        assert "invoice_exception" in keys

    def test_payment_request_without_ap_item(self):
        finance_email = SimpleNamespace(
            email_type="payment_request",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert "invoice_received" in keys
        assert "invoice_matched" in keys

    def test_receipt(self):
        finance_email = SimpleNamespace(
            email_type="receipt",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert keys == {"invoice_received"}

    def test_payment_confirmation(self):
        finance_email = SimpleNamespace(
            email_type="payment_confirmation",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert "invoice_paid" in keys

    def test_refund(self):
        finance_email = SimpleNamespace(
            email_type="refund",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert "finance_credit_note" in keys

    def test_credit_note(self):
        finance_email = SimpleNamespace(
            email_type="credit_note",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert "finance_credit_note" in keys

    def test_subject_hint_credit_note(self):
        ap_item = {
            "state": "needs_approval",
            "metadata": {"document_type": "invoice"},
        }
        finance_email = SimpleNamespace(
            subject="Credit note from Attio Limited for invoice #AW63GKYA-0003",
            email_type="invoice",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(ap_item=ap_item, finance_email=finance_email)
        assert "finance_credit_note" in keys
        assert "invoice_matched" in keys

    def test_subject_hint_refund(self):
        finance_email = SimpleNamespace(
            subject="Your refund from Cursor #3779-4144",
            email_type="invoice",
            status="processed",
            metadata={},
        )
        keys = finance_label_keys(finance_email=finance_email)
        assert "finance_credit_note" in keys


# ===========================================================================
# Legacy label cleanup
# ===========================================================================


class TestCleanupLegacyLabels:

    def test_migrates_flat_label_to_thesis_hierarchy(self):
        class _FakeClient:
            def __init__(self):
                self.list_messages = AsyncMock(return_value={"messages": [{"id": "msg-1"}, {"id": "msg-2"}]})
                self.add_label = AsyncMock(return_value=None)
                self.remove_label = AsyncMock(return_value=None)
                self.delete_label = AsyncMock(return_value=None)

            async def list_labels(self):
                return [
                    {"id": "old-invoices", "name": "Clearledgr/Invoices", "messagesTotal": 2},
                    {"id": "new-received", "name": "Clearledgr/Invoice/Received", "messagesTotal": 0},
                ]

            async def create_label(self, _name):
                raise AssertionError("should not create — canonical already exists")

        client = _FakeClient()
        result = asyncio.run(
            cleanup_legacy_labels(
                client,
                user_email="ops@example.com",
                dry_run=False,
                max_messages_per_label=100,
            )
        )
        assert result["labels_deleted"] >= 1
        assert result["messages_relabelled"] >= 2

    def test_skips_stale_label_without_target(self):
        class _FakeClient:
            def __init__(self):
                self.list_messages = AsyncMock(return_value={"messages": [{"id": "msg-1"}]})
                self.add_label = AsyncMock(return_value=None)
                self.remove_label = AsyncMock(return_value=None)
                self.delete_label = AsyncMock(return_value=None)

            async def list_labels(self):
                return [
                    {"id": "legacy-skipped", "name": "Clearledgr/Skipped", "messagesTotal": 1},
                ]

            async def create_label(self, _name):
                raise AssertionError("should not create for stale labels")

        client = _FakeClient()
        result = asyncio.run(
            cleanup_legacy_labels(
                client,
                user_email="ops@example.com",
                dry_run=False,
                max_messages_per_label=100,
            )
        )
        assert result["labels_deleted"] == 0
        assert result["results"][0]["delete_skipped_reason"] == "active_messages_without_migration_target"
        client.delete_label.assert_not_awaited()
