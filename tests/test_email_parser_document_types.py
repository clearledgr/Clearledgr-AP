from clearledgr.services.email_parser import EmailParser


def test_email_parser_classifies_refund_before_receipt():
    parser = EmailParser()

    parsed = parser.parse_email(
        subject="Your refund from Cursor #3779-4144",
        body="Refund receipt attached for your prior payment.",
        sender="billing@cursor.com",
        attachments=[],
    )

    assert parsed["email_type"] == "refund"


def test_email_parser_classifies_credit_note():
    parser = EmailParser()

    parsed = parser.parse_email(
        subject="Credit note from Attio Limited for invoice #AW63GKYA-0003",
        body="A credit note has been issued against your invoice.",
        sender="billing@attio.com",
        attachments=[],
    )

    assert parsed["email_type"] == "credit_note"


def test_email_parser_classifies_payment_confirmation_as_payment():
    parser = EmailParser()

    parsed = parser.parse_email(
        subject="Payment confirmation for invoice INV-2048",
        body="Payment processed successfully for your prior invoice.",
        sender="billing@acme.com",
        attachments=[],
    )

    assert parsed["email_type"] == "payment"


def test_email_parser_classifies_bank_statement():
    parser = EmailParser()

    parsed = parser.parse_email(
        subject="March bank statement for operating account",
        body="Your monthly account statement is ready.",
        sender="statements@bank.test",
        attachments=[],
    )

    assert parsed["email_type"] == "statement"
