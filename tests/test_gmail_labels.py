import asyncio

from types import SimpleNamespace
from unittest.mock import AsyncMock

from clearledgr.services.gmail_labels import cleanup_legacy_labels, finance_label_keys


def test_finance_label_keys_for_blocked_invoice_review():
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

    assert "processed" in keys
    assert "invoices" in keys
    assert "needs_approval" in keys
    assert "needs_review" in keys
    assert "exceptions" in keys


def test_finance_label_keys_for_payment_request_without_ap_item():
    finance_email = SimpleNamespace(
        email_type="payment_request",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert keys == {"processed", "payment_requests", "needs_approval"}


def test_finance_label_keys_for_receipt():
    finance_email = SimpleNamespace(
        email_type="receipt",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert keys == {"processed", "receipts"}


def test_finance_label_keys_for_payment_confirmation():
    finance_email = SimpleNamespace(
        email_type="payment_confirmation",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert keys == {"processed", "payments"}


def test_finance_label_keys_for_refund():
    finance_email = SimpleNamespace(
        email_type="refund",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert keys == {"processed", "refunds"}


def test_finance_label_keys_for_credit_note():
    finance_email = SimpleNamespace(
        email_type="credit_note",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert keys == {"processed", "credit_notes"}


def test_finance_label_keys_prefers_metadata_document_type_over_stale_email_type():
    finance_email = SimpleNamespace(
        email_type="invoice",
        status="processed",
        metadata={"document_type": "receipt"},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert "invoices" not in keys
    assert keys == {"processed", "receipts"}


def test_finance_label_keys_uses_refund_subject_hint_when_document_type_is_stale():
    finance_email = SimpleNamespace(
        subject="Your refund from Cursor #3779-4144",
        email_type="invoice",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(finance_email=finance_email)

    assert "invoices" not in keys
    assert keys == {"processed", "refunds"}


def test_finance_label_keys_subject_hint_overrides_stale_ap_metadata_document_type():
    ap_item = {
        "state": "needs_approval",
        "metadata": {
            "document_type": "invoice",
            "email_type": "invoice",
        },
    }
    finance_email = SimpleNamespace(
        subject="Credit note from Attio Limited for invoice #AW63GKYA-0003",
        email_type="invoice",
        status="processed",
        metadata={},
    )

    keys = finance_label_keys(ap_item=ap_item, finance_email=finance_email)

    assert "invoices" not in keys
    assert keys == {"processed", "credit_notes", "needs_approval"}


def test_cleanup_legacy_labels_migrates_and_deletes_alias_label():
    class _FakeClient:
        def __init__(self):
            self.list_messages = AsyncMock(return_value={"messages": [{"id": "msg-1"}, {"id": "msg-2"}]})
            self.add_label = AsyncMock(return_value=None)
            self.remove_label = AsyncMock(return_value=None)
            self.delete_label = AsyncMock(return_value=None)

        async def list_labels(self):
            return [
                {"id": "legacy-invoice", "name": "Clearledgr/Invoice", "messagesTotal": 2},
                {"id": "canonical-invoices", "name": "Clearledgr/Invoices", "messagesTotal": 0},
            ]

        async def create_label(self, _name):
            raise AssertionError("create_label should not be called when canonical label already exists")

    client = _FakeClient()
    result = asyncio.run(
        cleanup_legacy_labels(
            client,
            user_email="ops@example.com",
            dry_run=False,
            max_messages_per_label=100,
        )
    )

    assert result["labels_deleted"] == 1
    assert result["messages_relabelled"] == 2
    assert result["results"][0]["label_name"] == "Clearledgr/Invoice"
    assert result["results"][0]["target_labels"] == ["Clearledgr/Invoices"]
    assert client.add_label.await_count == 2
    assert client.remove_label.await_count == 2
    client.delete_label.assert_awaited_once_with("legacy-invoice")


def test_cleanup_legacy_labels_skips_active_stale_label_without_target():
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
            raise AssertionError("create_label should not be called for stale labels without migration targets")

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
