from __future__ import annotations

from clearledgr.core import database as db_module
from clearledgr.core.models import FinanceEmail


def _db(tmp_path, monkeypatch):
    db = db_module.get_db()
    db.initialize()
    return db


def test_save_finance_email_upsert_refreshes_extracted_fields(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db.save_finance_email(
        FinanceEmail(
            id="finance-email-1",
            organization_id="default",
            gmail_id="gmail-1",
            subject="Invoice detected",
            sender="billing@example.com",
            received_at="2026-03-19T20:00:00+00:00",
            email_type="invoice",
            confidence=0.81,
            status="detected",
            user_id="user-1",
            metadata={"classifier": {"type": "invoice", "confidence": 0.81}},
        )
    )

    db.save_finance_email(
        FinanceEmail(
            id="finance-email-1",
            organization_id="default",
            gmail_id="gmail-1",
            subject="Invoice INV-100",
            sender="Vendor Co <billing@vendor.test>",
            received_at="2026-03-19T20:00:00+00:00",
            email_type="invoice",
            confidence=0.97,
            vendor="Vendor Co",
            amount=451.23,
            currency="USD",
            invoice_number="INV-100",
            status="processed",
            processed_at="2026-03-19T20:01:00+00:00",
            user_id="user-1",
            metadata={
                "extraction_method": "regex_fallback",
                "raw_parser": {"has_invoice_attachment": True},
            },
        )
    )

    row = db.get_finance_email_by_gmail_id("gmail-1")

    assert row is not None
    assert row.subject == "Invoice INV-100"
    assert row.sender == "Vendor Co <billing@vendor.test>"
    assert row.vendor == "Vendor Co"
    assert row.amount == 451.23
    assert row.currency == "USD"
    assert row.invoice_number == "INV-100"
    assert row.status == "processed"
    assert row.metadata["extraction_method"] == "regex_fallback"
    assert row.metadata["raw_parser"]["has_invoice_attachment"] is True
