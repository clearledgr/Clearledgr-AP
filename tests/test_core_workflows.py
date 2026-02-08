from datetime import date

from clearledgr.models.requests import InvoiceExtractionRequest, ReconciliationRequest
from clearledgr.models.transactions import BankTransaction, GLTransaction, Money
from clearledgr.workflows.invoice import InvoiceWorkflow
from clearledgr.workflows.reconciliation import ReconciliationWorkflow


def test_reconciliation_workflow_matches():
    bank_txn = BankTransaction(
        transaction_id="bank_1",
        transaction_date=date(2025, 1, 10),
        description="Bank payment",
        counterparty="Acme",
        amount=Money(amount=100.0, currency="USD"),
    )
    gl_txn = GLTransaction(
        transaction_id="gl_1",
        transaction_date=date(2025, 1, 10),
        description="GL entry",
        counterparty="Acme",
        amount=Money(amount=100.0, currency="USD"),
    )

    payload = ReconciliationRequest(
        bank_transactions=[bank_txn],
        gl_transactions=[gl_txn],
    )

    workflow = ReconciliationWorkflow()
    result = workflow.run(
        {
            "bank_transactions": payload.bank_transactions,
            "gl_transactions": payload.gl_transactions,
            "config": payload.config,
        }
    )

    assert result.match_rate == 1.0
    assert len(result.matches) == 1
    assert not result.unmatched_bank
    assert not result.unmatched_gl


def test_invoice_workflow_extracts_basic_fields():
    payload = InvoiceExtractionRequest(
        email_subject="Invoice #INV-100 for services",
        email_sender="billing@acme.com",
        email_body="Total: $1,200.00\nInvoice INV-100\nDate: 2025-01-15",
    )

    workflow = InvoiceWorkflow()
    invoice = workflow.run(
        {
            "email_subject": payload.email_subject,
            "email_sender": payload.email_sender,
            "email_body": payload.email_body,
            "attachments": [],
            "invoice_id": "inv_100",
        }
    )

    assert invoice.invoice_id == "inv_100"
    assert invoice.extraction.invoice_number
    assert invoice.extraction.total is not None
