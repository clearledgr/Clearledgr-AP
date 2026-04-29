"""PEPPOL UBL 2.1 outbound generator (Wave 4 / F2).

Produces UBL 2.1 XML for two outbound document types Clearledgr
emits to vendors:

  * **CreditNote** (UBL CreditNote-2 schema, type code 381) — when
    the operator issues a vendor credit (e.g. partial dispute
    resolution, returned goods). Generated from a Clearledgr
    ``ap_items`` row plus a credit amount + reason.

  * **Invoice** — for organizations that receive the AP bill via
    Clearledgr but need to round-trip the PEPPOL UBL representation
    back to a vendor / portal that demands it (re-issue with
    corrected fields).

Both share the same supplier/customer/tax layout. The generator
follows BIS Billing 3.0 with the CustomizationID:
``urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0``

Output is byte-string XML (UTF-8) suitable for handing to a PEPPOL
Access Point or pasting into an email attachment.

The treatment-driven TaxCategory mapping is the inverse of the
inbound parser:
  domestic        → ID=S, Percent=<rate>
  reverse_charge  → ID=AE, Percent=0, ExemptionReason="Reverse charge…"
  zero_rated      → ID=Z, Percent=0
  exempt          → ID=E, Percent=0, ExemptionReason="Exempt…"
  out_of_scope    → ID=O, Percent=0
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)


_UBL_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_UBL_CREDITNOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
_CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"

_CUSTOMIZATION_ID = (
    "urn:cen.eu:en16931:2017"
    "#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
)
_PROFILE_INVOICE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
_PROFILE_CREDIT_NOTE = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


# Treatment → (TaxCategory ID, default ExemptionReason or None)
_TREATMENT_TO_CATEGORY: Dict[str, tuple[str, Optional[str]]] = {
    "domestic":       ("S",  None),
    "reverse_charge": ("AE", "Reverse charge - Article 196 of Directive 2006/112/EC"),
    "zero_rated":     ("Z",  None),
    "exempt":         ("E",  "Exempt supply"),
    "out_of_scope":   ("O",  "Out of scope of VAT"),
}


@dataclass
class UblParty:
    """Either a supplier (us, when generating a credit note) or
    customer (the vendor we're crediting). Same shape both sides."""

    name: str
    vat_id: Optional[str] = None
    company_id: Optional[str] = None
    street_name: Optional[str] = None
    city: Optional[str] = None
    postal_zone: Optional[str] = None
    country_code: Optional[str] = None


@dataclass
class UblLine:
    line_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_extension_amount: Decimal
    tax_category_id: str = "S"
    tax_percent: Decimal = Decimal("0")


@dataclass
class UblDocument:
    """Canonical input shape both invoice + credit-note generators
    take. The treatment field drives the TaxCategory ID across
    every TaxSubtotal."""

    document_id: str
    issue_date: str                            # YYYY-MM-DD
    currency: str
    supplier: UblParty
    customer: UblParty
    treatment: str                              # domestic / reverse_charge / ...
    line_extension_amount: Decimal              # sum of line nets
    tax_exclusive_amount: Decimal
    tax_inclusive_amount: Decimal
    payable_amount: Decimal
    tax_amount: Decimal
    tax_rate: Decimal                           # in percent (0..100)
    lines: List[UblLine] = field(default_factory=list)
    due_date: Optional[str] = None
    payment_terms_note: Optional[str] = None
    note: Optional[str] = None
    # Credit-note specific
    billing_reference_invoice_id: Optional[str] = None
    credit_reason: Optional[str] = None


def build_ubl_invoice(doc: UblDocument) -> bytes:
    """Generate a PEPPOL UBL Invoice XML payload (TypeCode 380)."""
    return _serialize(
        doc,
        root_tag="Invoice",
        type_code="380",
        namespace=_UBL_INVOICE_NS,
        line_tag="InvoiceLine",
        quantity_tag="InvoicedQuantity",
        profile=_PROFILE_INVOICE,
    )


def build_ubl_credit_note(doc: UblDocument) -> bytes:
    """Generate a PEPPOL UBL CreditNote XML payload (TypeCode 381)."""
    return _serialize(
        doc,
        root_tag="CreditNote",
        type_code="381",
        namespace=_UBL_CREDITNOTE_NS,
        line_tag="CreditNoteLine",
        quantity_tag="CreditedQuantity",
        profile=_PROFILE_CREDIT_NOTE,
    )


# ── Serializer ─────────────────────────────────────────────────────


def _money(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01')):.2f}"


def _percent(d: Decimal) -> str:
    return f"{d:f}".rstrip("0").rstrip(".") or "0"


def _e(value: Any) -> str:
    if value is None:
        return ""
    return _xml_escape(str(value))


def _serialize(
    doc: UblDocument,
    *,
    root_tag: str,
    type_code: str,
    namespace: str,
    line_tag: str,
    quantity_tag: str,
    profile: str,
) -> bytes:
    cat_id, exemption_default = _TREATMENT_TO_CATEGORY.get(
        doc.treatment, ("S", None),
    )
    exemption = exemption_default if cat_id in ("AE", "E", "O") else None

    parts: List[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<{root_tag} xmlns="{namespace}" '
        f'xmlns:cbc="{_CBC_NS}" '
        f'xmlns:cac="{_CAC_NS}">'
    )
    parts.append(f"<cbc:CustomizationID>{_e(_CUSTOMIZATION_ID)}</cbc:CustomizationID>")
    parts.append(f"<cbc:ProfileID>{_e(profile)}</cbc:ProfileID>")
    parts.append(f"<cbc:ID>{_e(doc.document_id)}</cbc:ID>")
    parts.append(f"<cbc:IssueDate>{_e(doc.issue_date)}</cbc:IssueDate>")
    if doc.due_date and root_tag == "Invoice":
        parts.append(f"<cbc:DueDate>{_e(doc.due_date)}</cbc:DueDate>")
    type_tag_name = (
        "InvoiceTypeCode" if root_tag == "Invoice" else "CreditNoteTypeCode"
    )
    parts.append(f"<cbc:{type_tag_name}>{_e(type_code)}</cbc:{type_tag_name}>")
    if doc.note:
        parts.append(f"<cbc:Note>{_e(doc.note)}</cbc:Note>")
    parts.append(
        f"<cbc:DocumentCurrencyCode>{_e(doc.currency)}</cbc:DocumentCurrencyCode>"
    )

    # Credit notes can carry a back-reference to the invoice they credit.
    if root_tag == "CreditNote" and doc.billing_reference_invoice_id:
        parts.append("<cac:BillingReference>")
        parts.append("<cac:InvoiceDocumentReference>")
        parts.append(
            f"<cbc:ID>{_e(doc.billing_reference_invoice_id)}</cbc:ID>"
        )
        parts.append("</cac:InvoiceDocumentReference>")
        parts.append("</cac:BillingReference>")

    parts.append(_party_xml("AccountingSupplierParty", doc.supplier))
    parts.append(_party_xml("AccountingCustomerParty", doc.customer))

    if doc.payment_terms_note:
        parts.append("<cac:PaymentTerms>")
        parts.append(f"<cbc:Note>{_e(doc.payment_terms_note)}</cbc:Note>")
        parts.append("</cac:PaymentTerms>")

    # TaxTotal — single subtotal at the bill level. Per-line tax info
    # also lands on each <line>/<Item>/<ClassifiedTaxCategory> below.
    parts.append("<cac:TaxTotal>")
    parts.append(
        f'<cbc:TaxAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.tax_amount)}</cbc:TaxAmount>'
    )
    parts.append("<cac:TaxSubtotal>")
    parts.append(
        f'<cbc:TaxableAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.tax_exclusive_amount)}</cbc:TaxableAmount>'
    )
    parts.append(
        f'<cbc:TaxAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.tax_amount)}</cbc:TaxAmount>'
    )
    parts.append("<cac:TaxCategory>")
    parts.append(f"<cbc:ID>{_e(cat_id)}</cbc:ID>")
    parts.append(f"<cbc:Percent>{_e(_percent(doc.tax_rate))}</cbc:Percent>")
    if exemption:
        parts.append(
            f"<cbc:TaxExemptionReason>{_e(exemption)}</cbc:TaxExemptionReason>"
        )
    parts.append('<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>')
    parts.append("</cac:TaxCategory>")
    parts.append("</cac:TaxSubtotal>")
    parts.append("</cac:TaxTotal>")

    parts.append("<cac:LegalMonetaryTotal>")
    parts.append(
        f'<cbc:LineExtensionAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.line_extension_amount)}</cbc:LineExtensionAmount>'
    )
    parts.append(
        f'<cbc:TaxExclusiveAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.tax_exclusive_amount)}</cbc:TaxExclusiveAmount>'
    )
    parts.append(
        f'<cbc:TaxInclusiveAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.tax_inclusive_amount)}</cbc:TaxInclusiveAmount>'
    )
    parts.append(
        f'<cbc:PayableAmount currencyID="{_e(doc.currency)}">'
        f'{_money(doc.payable_amount)}</cbc:PayableAmount>'
    )
    parts.append("</cac:LegalMonetaryTotal>")

    for line in doc.lines:
        parts.append(_line_xml(
            line, line_tag=line_tag, quantity_tag=quantity_tag,
            currency=doc.currency, doc_treatment=doc.treatment,
        ))

    parts.append(f"</{root_tag}>")
    return "".join(parts).encode("utf-8")


def _party_xml(role_tag: str, party: UblParty) -> str:
    parts: List[str] = [f"<cac:{role_tag}>", "<cac:Party>"]
    parts.append(f"<cac:PartyName><cbc:Name>{_e(party.name)}</cbc:Name></cac:PartyName>")
    if party.street_name or party.city or party.postal_zone or party.country_code:
        parts.append("<cac:PostalAddress>")
        if party.street_name:
            parts.append(f"<cbc:StreetName>{_e(party.street_name)}</cbc:StreetName>")
        if party.city:
            parts.append(f"<cbc:CityName>{_e(party.city)}</cbc:CityName>")
        if party.postal_zone:
            parts.append(f"<cbc:PostalZone>{_e(party.postal_zone)}</cbc:PostalZone>")
        if party.country_code:
            parts.append(
                "<cac:Country>"
                f"<cbc:IdentificationCode>{_e(party.country_code)}</cbc:IdentificationCode>"
                "</cac:Country>"
            )
        parts.append("</cac:PostalAddress>")
    if party.vat_id:
        parts.append("<cac:PartyTaxScheme>")
        parts.append(f"<cbc:CompanyID>{_e(party.vat_id)}</cbc:CompanyID>")
        parts.append('<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>')
        parts.append("</cac:PartyTaxScheme>")
    if party.company_id or party.name:
        parts.append("<cac:PartyLegalEntity>")
        parts.append(
            f"<cbc:RegistrationName>{_e(party.name)}</cbc:RegistrationName>"
        )
        if party.company_id:
            parts.append(
                f'<cbc:CompanyID schemeID="0002">'
                f'{_e(party.company_id)}</cbc:CompanyID>'
            )
        parts.append("</cac:PartyLegalEntity>")
    parts.append("</cac:Party>")
    parts.append(f"</cac:{role_tag}>")
    return "".join(parts)


def _line_xml(
    line: UblLine,
    *,
    line_tag: str,
    quantity_tag: str,
    currency: str,
    doc_treatment: str,
) -> str:
    cat_id, _ = _TREATMENT_TO_CATEGORY.get(doc_treatment, ("S", None))
    parts: List[str] = [f"<cac:{line_tag}>"]
    parts.append(f"<cbc:ID>{_e(line.line_id)}</cbc:ID>")
    parts.append(
        f'<cbc:{quantity_tag} unitCode="EA">'
        f'{line.quantity}</cbc:{quantity_tag}>'
    )
    parts.append(
        f'<cbc:LineExtensionAmount currencyID="{_e(currency)}">'
        f'{_money(line.line_extension_amount)}</cbc:LineExtensionAmount>'
    )
    parts.append("<cac:Item>")
    parts.append(f"<cbc:Name>{_e(line.description)}</cbc:Name>")
    parts.append("<cac:ClassifiedTaxCategory>")
    parts.append(f"<cbc:ID>{_e(line.tax_category_id or cat_id)}</cbc:ID>")
    parts.append(
        f"<cbc:Percent>{_e(_percent(line.tax_percent))}</cbc:Percent>"
    )
    parts.append('<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>')
    parts.append("</cac:ClassifiedTaxCategory>")
    parts.append("</cac:Item>")
    parts.append("<cac:Price>")
    parts.append(
        f'<cbc:PriceAmount currencyID="{_e(currency)}">'
        f'{_money(line.unit_price)}</cbc:PriceAmount>'
    )
    parts.append("</cac:Price>")
    parts.append(f"</cac:{line_tag}>")
    return "".join(parts)


# ── Convenience: build from an AP item + organization ──────────────


def build_credit_note_from_ap_item(
    *,
    ap_item: Dict[str, Any],
    organization: Dict[str, Any],
    credit_amount: Decimal,
    credit_reason: str,
    credit_note_id: Optional[str] = None,
    issue_date: Optional[str] = None,
) -> bytes:
    """Convenience wrapper: build a UBL CreditNote crediting one
    ap_items row.

    The supplier on the credit note is *us* (the organization
    issuing the credit); the customer is the original *vendor*.

    ``credit_amount`` is the GROSS amount being credited (vendor's
    refund). The treatment + rate are inherited from the AP item so
    the credit-note tax line mirrors the original invoice.
    """
    org_country = (
        (organization.get("settings") or {}).get("tax", {}).get("home_country")
        or "GB"
    )

    treatment = str(ap_item.get("tax_treatment") or "domestic")
    rate = Decimal(str(ap_item.get("vat_rate") or 0))

    # Split credit_amount into net + VAT using the same rate.
    if treatment == "domestic" and rate > 0:
        factor = (Decimal("100") + rate) / Decimal("100")
        net = (credit_amount / factor).quantize(Decimal("0.01"))
        vat = (credit_amount - net).quantize(Decimal("0.01"))
    elif treatment == "reverse_charge":
        net = credit_amount
        vat = (
            (net * rate / Decimal("100")).quantize(Decimal("0.01"))
            if rate > 0 else Decimal("0.00")
        )
    else:
        net = credit_amount
        vat = Decimal("0.00")

    supplier = UblParty(
        name=str(
            organization.get("name")
            or organization.get("organization_name")
            or organization.get("id")
            or "Issuer"
        ),
        country_code=org_country,
    )
    customer = UblParty(
        name=str(ap_item.get("vendor_name") or "Vendor"),
        country_code=str(ap_item.get("bill_country") or ""),
    )
    cat_id, _ = _TREATMENT_TO_CATEGORY.get(treatment, ("S", None))

    issue = issue_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cn_id = credit_note_id or f"CN-{ap_item.get('id') or 'X'}"

    line = UblLine(
        line_id="1",
        description=(
            f"Credit for invoice {ap_item.get('invoice_number') or ap_item.get('id')}"
        ),
        quantity=Decimal("1"),
        unit_price=net,
        line_extension_amount=net,
        tax_category_id=cat_id,
        tax_percent=rate,
    )

    doc = UblDocument(
        document_id=cn_id,
        issue_date=issue,
        currency=str(ap_item.get("currency") or "EUR"),
        supplier=supplier,
        customer=customer,
        treatment=treatment,
        line_extension_amount=net,
        tax_exclusive_amount=net,
        tax_inclusive_amount=(
            net + vat if treatment == "domestic" else net
        ),
        payable_amount=(
            net + vat if treatment == "domestic" else net
        ),
        tax_amount=vat,
        tax_rate=rate,
        lines=[line],
        billing_reference_invoice_id=ap_item.get("invoice_number"),
        credit_reason=credit_reason,
        note=credit_reason,
    )
    return build_ubl_credit_note(doc)
