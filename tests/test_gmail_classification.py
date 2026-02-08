from clearledgr.services.ap_classifier import classify_ap_email
from clearledgr.services.email_parser import EmailParser


def test_invoice_classification():
    result = classify_ap_email(
        subject="Invoice #12345 available",
        sender="billing@vendor.com",
        snippet="Amount due $499.00",
        body=""
    )
    assert result["type"] == "INVOICE"
    assert result["confidence"] >= 0.8


def test_payment_request_classification():
    result = classify_ap_email(
        subject="Payment request for services",
        sender="contractor@vendor.com",
        snippet="Please pay $1,200 this week",
        body=""
    )
    assert result["type"] == "PAYMENT_REQUEST"
    assert result["confidence"] >= 0.8


def test_marketing_filtered():
    result = classify_ap_email(
        subject="Special offer - 50% off",
        sender="news@vendor.com",
        snippet="Unsubscribe at any time",
        body=""
    )
    assert result["type"] == "NOISE"


def test_parser_ignores_year_as_amount():
    parser = EmailParser()
    text = "Summary for 1 Dec 2025 - 31 Dec 2025. Total in USD 0.00."
    parsed = parser.parse_invoice_text(text)
    amount = parsed.get("amount")
    assert amount is not None
    assert amount.get("value") == 0.0


def test_parser_avoids_invoice_number_as_amount():
    parser = EmailParser()
    text = "Invoice number 5449235811. Total in EUR 40.23."
    parsed = parser.parse_invoice_text(text)
    amount = parsed.get("amount")
    assert amount is not None
    assert amount.get("value") == 40.23
