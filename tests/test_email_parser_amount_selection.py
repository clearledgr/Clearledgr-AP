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


def test_pdf_attachment_promotes_invoice_amount_when_email_body_has_no_total(monkeypatch):
    parser = EmailParser()

    monkeypatch.setattr(
        parser,
        "_extract_pdf_text",
        lambda _content_base64, max_pages=None: (
            "Google Invoice\n"
            "Invoice number: 5449235811\n"
            "Invoice date 31 Dec 2025\n"
            "Total in EUR €40.23\n"
        ),
    )

    parsed = parser.parse_email(
        sender="Google Payments <payments-noreply@google.com>",
        subject="Google Workspace: Your invoice is available for clearledgr.com",
        body="Your Google Workspace monthly invoice is available. Please find the PDF attached.",
        attachments=[
            {
                "filename": "5449235811.pdf",
                "content_type": "application/pdf",
                "content_base64": "ZHVtbXk=",
            }
        ],
    )

    assert parsed["primary_invoice"] == "5449235811"
    assert parsed["primary_amount"] == 40.23
    assert parsed["currency"] == "EUR"


def test_invoice_text_ignores_currency_noise_when_extracting_vendor():
    parser = EmailParser()

    parsed = parser.parse_invoice_text(
        "--- Page 1 Tables ---\n"
        ".In..v.o..ic..e. .d.a..t.e..\n"
        "Subtotal in USD\n"
        "US$0.00\n"
        "--- Page 1 Text ---\n"
        "Google Cloud EMEA Limited\n"
        "Velasco\n"
        "Invoice number: 5515266827\n"
        "Subtotal in USD US$0.00\n"
        "VAT (0%) US$0.00\n"
        "Total in USD US$0.00\n"
    )

    assert parsed["vendor"] == "Google Cloud EMEA Limited"
    assert parsed["invoice_number"] == "5515266827"
    assert parsed["amount"]["value"] == 0.0
    assert parsed["currency"] == "USD"


def test_scanned_pdf_ocr_fallback_recovers_invoice_fields(monkeypatch):
    parser = EmailParser()

    monkeypatch.setattr(parser, "_extract_with_pdfplumber", lambda _data, max_pages=None: None)
    monkeypatch.setattr(parser, "_extract_with_pypdf2", lambda _data, max_pages=None: None)
    monkeypatch.setattr(
        parser,
        "_extract_pdf_text_ocr",
        lambda _data, max_pages=None: (
            "Google Cloud EMEA Limited\n"
            "Invoice number: 5515266827\n"
            "Invoice date: 31 Dec 2025\n"
            "Total in USD US$40.00\n"
        ),
    )

    parsed = parser.parse_email(
        sender="billing@google.com",
        subject="Invoice attached",
        body="Please see attached invoice.",
        attachments=[
            {
                "filename": "invoice.pdf",
                "content_type": "application/pdf",
                "content_base64": "ZHVtbXk=",
            }
        ],
    )

    assert parsed["primary_invoice"] == "5515266827"
    assert parsed["primary_amount"] == 40.0
    assert parsed["currency"] == "USD"
    assert parsed["vendor"] == "Google Cloud EMEA Limited"


def test_attachment_mismatch_returns_provenance_and_blocking_conflict(monkeypatch):
    parser = EmailParser()

    monkeypatch.setattr(
        parser,
        "_extract_pdf_text",
        lambda _content_base64, max_pages=None: (
            "Acme Corp\n"
            "Invoice number: INV-ACME-77\n"
            "Invoice date 31 Dec 2025\n"
            "Total in USD $440.00\n"
        ),
    )

    parsed = parser.parse_email(
        sender="billing@acme.test",
        subject="Invoice INV-ACME-77",
        body="Invoice INV-ACME-77\nTotal Due: $400.00",
        attachments=[
            {
                "filename": "INV-ACME-77.pdf",
                "content_type": "application/pdf",
                "content_base64": "ZHVtbXk=",
            }
        ],
    )

    assert parsed["primary_amount"] == 440.0
    assert parsed["field_provenance"]["amount"]["source"] == "attachment"
    amount_conflict = next(item for item in parsed["source_conflicts"] if item["field"] == "amount")
    assert amount_conflict["blocking"] is True
    assert amount_conflict["values"]["email"] == 400.0
    assert amount_conflict["values"]["attachment"] == 440.0
    assert parsed["requires_extraction_review"] is True
