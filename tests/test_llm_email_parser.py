from clearledgr.services import llm_email_parser as llm_email_parser_module
from clearledgr.services.llm_email_parser import LLMEmailParser, _merge_attachment_evidence


def test_llm_result_maps_credit_note_document_type():
    parsed = llm_email_parser_module._llm_result_to_parse_email_dict(
        {
            "document_type": "credit_note",
            "vendor": "Attio Limited",
            "payment_processor": None,
            "amount": 10.0,
            "currency": "USD",
            "invoice_number": "CN-10",
            "invoice_date": None,
            "due_date": None,
            "field_confidences": {},
            "confidence": 0.91,
            "reasoning": "Credit note detected.",
        },
        sender="billing@attio.com",
        subject="Credit note from Attio Limited",
        attachments=[],
        model="test-model",
    )

    assert parsed["email_type"] == "credit_note"
    assert parsed["document_type"] == "credit_note"


def test_llm_result_maps_payment_document_type():
    parsed = llm_email_parser_module._llm_result_to_parse_email_dict(
        {
            "document_type": "payment",
            "vendor": "Acme Corp",
            "payment_processor": None,
            "amount": 42.0,
            "currency": "USD",
            "invoice_number": "INV-42",
            "invoice_date": None,
            "due_date": None,
            "field_confidences": {},
            "confidence": 0.88,
            "reasoning": "Payment confirmation detected.",
        },
        sender="billing@acme.com",
        subject="Payment confirmation",
        attachments=[],
        model="test-model",
    )

    assert parsed["email_type"] == "payment"
    assert parsed["document_type"] == "payment"


def test_attachment_evidence_promotes_missing_llm_fields():
    llm_result = {
        "email_type": "invoice",
        "document_type": "invoice",
        "vendor": "Stripe",
        "payment_processor": "Stripe",
        "primary_amount": 0.0,
        "amounts": [{"value": 0.0, "raw": "0.0", "currency": "USD"}],
        "currency": "USD",
        "primary_invoice": None,
        "invoice_numbers": [],
        "dates": [],
        "primary_date": None,
        "due_date": None,
        "field_confidences": {"amount": 0.42, "invoice_number": 0.11, "vendor": 0.35},
        "attachments": [{"type": "document", "parsed": False}],
        "has_invoice_attachment": True,
        "has_statement_attachment": False,
        "primary_source": "attachment",
        "reasoning_summary": "Invoice detected.",
        "extraction_method": "llm",
    }
    local_result = {
        "email_type": "invoice",
        "vendor": "Google Cloud EMEA Limited",
        "primary_amount": 40.23,
        "amounts": [{"value": 40.23, "raw": "40.23", "currency": "EUR"}],
        "currency": "EUR",
        "primary_invoice": "5449235811",
        "invoice_numbers": ["5449235811"],
        "dates": ["2025-12-31"],
        "primary_date": "2025-12-31",
        "due_date": "2026-01-15",
        "field_confidences": {"amount": 0.95, "invoice_number": 0.93, "vendor": 0.91, "due_date": 0.9},
        "attachments": [{"type": "invoice", "parsed": True}],
        "has_invoice_attachment": True,
        "has_statement_attachment": False,
        "primary_source": "attachment",
    }

    merged = _merge_attachment_evidence(llm_result, local_result)

    assert merged["vendor"] == "Google Cloud EMEA Limited"
    assert merged["primary_amount"] == 40.23
    assert merged["currency"] == "EUR"
    assert merged["primary_invoice"] == "5449235811"
    assert merged["due_date"] == "2026-01-15"
    assert merged["extraction_method"] == "llm+attachment_evidence"
    assert "Attachment evidence strengthened" in merged["reasoning_summary"]


def test_attachment_evidence_does_not_override_strong_llm_amount():
    llm_result = {
        "email_type": "invoice",
        "document_type": "invoice",
        "vendor": "Acme Corp",
        "payment_processor": None,
        "primary_amount": 125.0,
        "amounts": [{"value": 125.0, "raw": "125.0", "currency": "USD"}],
        "currency": "USD",
        "primary_invoice": "INV-125",
        "invoice_numbers": ["INV-125"],
        "dates": ["2026-03-19"],
        "primary_date": "2026-03-19",
        "due_date": "2026-04-18",
        "field_confidences": {"amount": 0.97, "invoice_number": 0.96, "vendor": 0.95, "due_date": 0.94},
        "attachments": [{"type": "document", "parsed": False}],
        "has_invoice_attachment": True,
        "has_statement_attachment": False,
        "primary_source": "attachment",
        "reasoning_summary": "Invoice detected.",
        "extraction_method": "llm",
    }
    local_result = {
        "email_type": "invoice",
        "vendor": "Acme Corp",
        "primary_amount": 0.0,
        "amounts": [{"value": 0.0, "raw": "0.0", "currency": "USD"}],
        "currency": "USD",
        "primary_invoice": "INV-125",
        "invoice_numbers": ["INV-125"],
        "dates": ["2026-03-19"],
        "primary_date": "2026-03-19",
        "due_date": "2026-04-18",
        "field_confidences": {"amount": 0.7},
        "attachments": [{"type": "invoice", "parsed": True}],
        "has_invoice_attachment": True,
        "has_statement_attachment": False,
        "primary_source": "attachment",
    }

    merged = _merge_attachment_evidence(llm_result, local_result)

    assert merged["primary_amount"] == 125.0
    assert merged["currency"] == "USD"
    assert merged["extraction_method"] == "llm"


def test_authoritative_attachment_result_skips_llm(monkeypatch):
    parser = LLMEmailParser()
    parser._api_key = "test-api-key"

    local_result = {
        "email_type": "invoice",
        "document_type": "invoice",
        "vendor": "Google Cloud EMEA Limited",
        "primary_amount": 40.23,
        "amounts": [{"value": 40.23, "raw": "40.23", "currency": "EUR"}],
        "currency": "EUR",
        "primary_invoice": "5449235811",
        "invoice_numbers": ["5449235811"],
        "dates": ["2025-12-31"],
        "primary_date": "2025-12-31",
        "due_date": "2026-01-15",
        "attachments": [{"type": "invoice", "parsed": True}],
        "has_invoice_attachment": True,
        "has_statement_attachment": False,
        "primary_source": "attachment",
        "confidence": 0.86,
    }

    monkeypatch.setattr(
        "clearledgr.services.email_parser.EmailParser.parse_email",
        lambda self, subject, body, sender, attachments=None: dict(local_result),
    )

    def _unexpected_claude_call(*_args, **_kwargs):
        raise AssertionError("Claude should not be called for authoritative attachment extraction")

    monkeypatch.setattr(llm_email_parser_module, "_call_claude_vision", _unexpected_claude_call)

    result = parser.parse_email(
        subject="Invoice attached",
        body="Please see attached invoice.",
        sender="billing@google.test",
        attachments=[
            {
                "filename": "5449235811.pdf",
                "content_type": "application/pdf",
                "content_base64": "ZHVtbXk=",
            }
        ],
    )

    assert result["extraction_method"] == "attachment_authoritative"
    assert result["vendor"] == "Google Cloud EMEA Limited"
    assert result["primary_amount"] == 40.23
    assert result["field_confidences"]["amount"] >= 0.95
