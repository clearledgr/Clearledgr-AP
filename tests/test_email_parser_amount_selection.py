from clearledgr.services.email_parser import EmailParser


def test_payment_request_extracts_amount_via_fallback():
    parser = EmailParser()

    parsed = parser.parse_email(
        sender="contractor@freelance.dev",
        subject="Payment request for February work",
        body="Please pay $2,000.00 for February retainer. Thank you.",
    )

    assert parsed["email_type"] == "payment_request"
    assert parsed["primary_amount"] == 2000.0
    assert parsed["currency"] == "USD"


def test_total_due_outranks_subtotal_tax_and_discount():
    parser = EmailParser()

    parsed = parser.parse_email(
        sender="ar@designco.com",
        subject="Invoice INV-7788",
        body=(
            "Subtotal: $300.00\n"
            "Tax: $22.00\n"
            "Discount: $42.00\n"
            "Total Due: $280.00\n"
            "Invoice # INV-7788"
        ),
    )

    assert parsed["email_type"] == "invoice"
    assert parsed["primary_invoice"] == "INV-7788"
    assert parsed["primary_amount"] == 280.0
    assert parsed["currency"] == "USD"


def test_vendor_fuzzy_matching_avoids_false_positive_salesforce():
    parser = EmailParser()

    parsed = parser.parse_email(
        sender="ap@taskforce.dev",
        subject="Payment request for support work",
        body="Please pay $420.00 for on-call support.",
    )

    assert parsed["vendor"] == "Taskforce"
