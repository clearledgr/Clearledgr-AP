"""PEPPOL UBL 2.1 invoice parser (Wave 4 / F1).

Parses PEPPOL BIS Billing 3.0 invoices (UBL 2.1 XML) into Clearledgr's
canonical :class:`InvoiceData` shape so the rest of the pipeline
(validation gate, VAT calculator, JE preview, ERP post) treats a
PEPPOL-delivered bill identically to a Gmail-attached PDF or a
NetSuite-pushed bill event.

Why PEPPOL: by 2028, EU member states (Germany, France, Italy,
Belgium, Spain) will require structured electronic invoicing for
B2B transactions. PEPPOL is the de-facto wire-format. The UK
HMRC's own e-invoicing consultation (2024) signals the same
direction post-2027.

Schema: BIS Billing 3.0 (https://docs.peppol.eu/poacc/billing/3.0/)
Root: ``<Invoice>`` in namespace
``urn:oasis:names:specification:ubl:schema:xsd:Invoice-2`` with
child elements in cbc + cac namespaces.

Tax category codes mapped to Clearledgr treatments:

  ``S``  (Standard rate)        → domestic
  ``Z``  (Zero rated)            → zero_rated
  ``E``  (Exempt)                → exempt
  ``AE`` (Reverse charge)        → reverse_charge
  ``K``  (Intra-EU goods)        → reverse_charge
  ``G``  (Export outside EU)     → zero_rated
  ``O``  (Out of scope of VAT)   → out_of_scope

This parser is **stdlib-only** (xml.etree). No external dep — same
discipline as the bank statement parsers in C6.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


_UBL_NS_RE = re.compile(r"^\{[^}]+\}")


def _strip_ns(tag: str) -> str:
    return _UBL_NS_RE.sub("", tag)


def _find(elem: Optional[ET.Element], path: List[str]) -> Optional[ET.Element]:
    """Walk a path of localnames through namespaced UBL XML."""
    if elem is None:
        return None
    cur = elem
    for step in path:
        nxt = None
        for child in list(cur):
            if _strip_ns(child.tag) == step:
                nxt = child
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _findall(elem: Optional[ET.Element], path: List[str]) -> List[ET.Element]:
    if elem is None:
        return []
    if not path:
        return [elem]
    if len(path) == 1:
        return [c for c in list(elem) if _strip_ns(c.tag) == path[0]]
    head, tail = path[0], path[1:]
    out: List[ET.Element] = []
    for child in list(elem):
        if _strip_ns(child.tag) == head:
            out.extend(_findall(child, tail))
    return out


def _text(elem: Optional[ET.Element]) -> Optional[str]:
    if elem is None or elem.text is None:
        return None
    s = elem.text.strip()
    return s or None


def _decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


# ── Tax category mapping ───────────────────────────────────────────


_TAX_CATEGORY_TO_TREATMENT: Dict[str, str] = {
    "S":  "domestic",
    "Z":  "zero_rated",
    "E":  "exempt",
    "AE": "reverse_charge",
    "K":  "reverse_charge",
    "G":  "zero_rated",
    "O":  "out_of_scope",
    "L":  "domestic",
    "M":  "domestic",
}


_TAX_CATEGORY_TO_VAT_CODE: Dict[str, str] = {
    "S":  "T1",
    "Z":  "T0",
    "E":  "T2",
    "AE": "RC",
    "K":  "RC",
    "G":  "T0",
    "O":  "OO",
}


# ── Output shape ────────────────────────────────────────────────────


@dataclass
class ParsedPeppolInvoice:
    """Canonical extraction from one PEPPOL UBL invoice."""

    customization_id: Optional[str] = None
    profile_id: Optional[str] = None
    invoice_id: Optional[str] = None
    invoice_type_code: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None

    # Supplier (vendor)
    supplier_name: Optional[str] = None
    supplier_vat_id: Optional[str] = None
    supplier_company_id: Optional[str] = None
    supplier_country: Optional[str] = None
    supplier_address_line: Optional[str] = None
    supplier_city: Optional[str] = None
    supplier_postal_zone: Optional[str] = None

    # Customer (us)
    customer_name: Optional[str] = None
    customer_vat_id: Optional[str] = None

    # Monetary totals
    line_extension_amount: Optional[Decimal] = None
    tax_exclusive_amount: Optional[Decimal] = None
    tax_inclusive_amount: Optional[Decimal] = None
    payable_amount: Optional[Decimal] = None

    # Aggregate tax
    tax_amount: Optional[Decimal] = None
    # Per-subtotal tax breakdown (one entry per rate / category)
    tax_subtotals: List[Dict[str, Any]] = field(default_factory=list)

    # Derived treatment + rate (worst-case wins: if any subtotal is RC,
    # the whole bill is treated as RC for routing).
    derived_treatment: Optional[str] = None
    derived_vat_code: Optional[str] = None
    derived_vat_rate: Optional[Decimal] = None

    # Lines
    line_items: List[Dict[str, Any]] = field(default_factory=list)

    # Payment terms
    payment_terms: Optional[str] = None

    # Validation issues found while parsing
    warnings: List[str] = field(default_factory=list)

    def to_invoice_data_kwargs(self) -> Dict[str, Any]:
        """Subset suitable for constructing :class:`InvoiceData`."""
        return {
            "vendor_name": self.supplier_name or "",
            "amount": (
                float(self.payable_amount)
                if self.payable_amount is not None else 0.0
            ),
            "currency": self.currency or "EUR",
            "invoice_number": self.invoice_id,
            "due_date": self.due_date,
            "tax_amount": (
                float(self.tax_amount) if self.tax_amount is not None else None
            ),
            "tax_rate": (
                float(self.derived_vat_rate)
                if self.derived_vat_rate is not None else None
            ),
            "subtotal": (
                float(self.tax_exclusive_amount)
                if self.tax_exclusive_amount is not None else None
            ),
            "line_items": list(self.line_items) if self.line_items else None,
            "payment_terms": self.payment_terms,
        }


# ── Parser entry points ────────────────────────────────────────────


def parse_peppol_ubl_invoice(content: bytes) -> ParsedPeppolInvoice:
    """Parse a single PEPPOL UBL Invoice XML payload.

    Returns a :class:`ParsedPeppolInvoice` even on partial/missing
    fields — every problem is appended to ``warnings`` and the
    extraction proceeds best-effort. A completely unparseable
    document yields an empty record with a single warning.
    """
    out = ParsedPeppolInvoice()
    if not content:
        out.warnings.append("empty_body")
        return out

    text = content.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        out.warnings.append(f"xml_parse_error:{exc}")
        return out

    if _strip_ns(root.tag) not in ("Invoice", "CreditNote"):
        out.warnings.append(
            f"unexpected_root:{_strip_ns(root.tag)}"
        )
        return out

    out.customization_id = _text(_find(root, ["CustomizationID"]))
    out.profile_id = _text(_find(root, ["ProfileID"]))
    out.invoice_id = _text(_find(root, ["ID"]))
    out.invoice_type_code = _text(_find(root, ["InvoiceTypeCode"]))
    out.issue_date = _text(_find(root, ["IssueDate"]))
    out.due_date = _text(_find(root, ["DueDate"]))
    out.currency = _text(_find(root, ["DocumentCurrencyCode"]))

    _parse_supplier(root, out)
    _parse_customer(root, out)
    _parse_legal_monetary_total(root, out)
    _parse_tax_total(root, out)
    _parse_invoice_lines(root, out)
    _parse_payment_terms(root, out)

    _derive_treatment(out)
    _validate(out)

    return out


def _parse_supplier(root: ET.Element, out: ParsedPeppolInvoice) -> None:
    sup = _find(root, ["AccountingSupplierParty", "Party"])
    if sup is None:
        out.warnings.append("missing_supplier_party")
        return
    out.supplier_name = (
        _text(_find(sup, ["PartyLegalEntity", "RegistrationName"]))
        or _text(_find(sup, ["PartyName", "Name"]))
    )
    out.supplier_vat_id = _text(
        _find(sup, ["PartyTaxScheme", "CompanyID"])
    )
    out.supplier_company_id = _text(
        _find(sup, ["PartyLegalEntity", "CompanyID"])
    )
    addr = _find(sup, ["PostalAddress"])
    if addr is not None:
        out.supplier_address_line = _text(_find(addr, ["StreetName"]))
        out.supplier_city = _text(_find(addr, ["CityName"]))
        out.supplier_postal_zone = _text(_find(addr, ["PostalZone"]))
        out.supplier_country = _text(
            _find(addr, ["Country", "IdentificationCode"])
        )


def _parse_customer(root: ET.Element, out: ParsedPeppolInvoice) -> None:
    cust = _find(root, ["AccountingCustomerParty", "Party"])
    if cust is None:
        return
    out.customer_name = (
        _text(_find(cust, ["PartyLegalEntity", "RegistrationName"]))
        or _text(_find(cust, ["PartyName", "Name"]))
    )
    out.customer_vat_id = _text(
        _find(cust, ["PartyTaxScheme", "CompanyID"])
    )


def _parse_legal_monetary_total(
    root: ET.Element, out: ParsedPeppolInvoice,
) -> None:
    lmt = _find(root, ["LegalMonetaryTotal"])
    if lmt is None:
        out.warnings.append("missing_legal_monetary_total")
        return
    out.line_extension_amount = _decimal(_text(_find(lmt, ["LineExtensionAmount"])))
    out.tax_exclusive_amount = _decimal(_text(_find(lmt, ["TaxExclusiveAmount"])))
    out.tax_inclusive_amount = _decimal(_text(_find(lmt, ["TaxInclusiveAmount"])))
    out.payable_amount = _decimal(_text(_find(lmt, ["PayableAmount"])))


def _parse_tax_total(
    root: ET.Element, out: ParsedPeppolInvoice,
) -> None:
    tt = _find(root, ["TaxTotal"])
    if tt is None:
        out.warnings.append("missing_tax_total")
        return
    out.tax_amount = _decimal(_text(_find(tt, ["TaxAmount"])))
    for sub in _findall(tt, ["TaxSubtotal"]):
        cat = _find(sub, ["TaxCategory"])
        cat_id = _text(_find(cat, ["ID"])) if cat is not None else None
        percent = _decimal(
            _text(_find(cat, ["Percent"])) if cat is not None else None
        )
        out.tax_subtotals.append({
            "taxable_amount": _decimal(_text(_find(sub, ["TaxableAmount"]))),
            "tax_amount": _decimal(_text(_find(sub, ["TaxAmount"]))),
            "category_id": cat_id,
            "percent": percent,
            "tax_scheme": _text(
                _find(cat, ["TaxScheme", "ID"]) if cat is not None else None
            ),
            "exemption_reason": _text(
                _find(cat, ["TaxExemptionReason"]) if cat is not None else None
            ),
        })


def _parse_invoice_lines(
    root: ET.Element, out: ParsedPeppolInvoice,
) -> None:
    for line in _findall(root, ["InvoiceLine"]) + _findall(root, ["CreditNoteLine"]):
        item: Dict[str, Any] = {}
        item["id"] = _text(_find(line, ["ID"]))
        # InvoiceLine has InvoicedQuantity, CreditNoteLine has CreditedQuantity.
        qty_el = _find(line, ["InvoicedQuantity"])
        if qty_el is None:
            qty_el = _find(line, ["CreditedQuantity"])
        item["quantity"] = (
            float(_decimal(_text(qty_el)) or Decimal("0"))
            if qty_el is not None else None
        )
        item["amount"] = float(
            _decimal(_text(_find(line, ["LineExtensionAmount"]))) or Decimal("0")
        )
        item["description"] = _text(
            _find(line, ["Item", "Name"])
        ) or _text(_find(line, ["Item", "Description"]))
        unit_price = _decimal(
            _text(_find(line, ["Price", "PriceAmount"]))
        )
        item["unit_price"] = (
            float(unit_price) if unit_price is not None else None
        )
        cat_id = _text(
            _find(line, ["Item", "ClassifiedTaxCategory", "ID"])
        )
        cat_pct = _decimal(
            _text(_find(line, ["Item", "ClassifiedTaxCategory", "Percent"]))
        )
        if cat_id:
            item["tax_category_id"] = cat_id
        if cat_pct is not None:
            item["tax_rate"] = float(cat_pct)
        out.line_items.append(item)


def _parse_payment_terms(
    root: ET.Element, out: ParsedPeppolInvoice,
) -> None:
    note = _text(_find(root, ["PaymentTerms", "Note"]))
    if note:
        out.payment_terms = note


def _derive_treatment(out: ParsedPeppolInvoice) -> None:
    """Roll up per-subtotal tax categories to one bill-level treatment.

    Reverse charge wins over everything else (any RC line forces RC
    treatment for routing — buyer self-accounts on the whole bill).
    """
    if not out.tax_subtotals:
        # No tax breakdown — try to infer from totals.
        if (
            out.tax_amount is not None
            and out.tax_exclusive_amount is not None
            and out.tax_amount == 0
        ):
            out.derived_treatment = "zero_rated"
            out.derived_vat_code = "T0"
            out.derived_vat_rate = Decimal("0")
        else:
            out.derived_treatment = "domestic"
            out.derived_vat_code = "T1"
        return

    # Reverse charge dominance.
    rc_subs = [s for s in out.tax_subtotals if s.get("category_id") in ("AE", "K")]
    if rc_subs:
        out.derived_treatment = "reverse_charge"
        out.derived_vat_code = "RC"
        rates = [s.get("percent") for s in rc_subs if s.get("percent") is not None]
        if rates:
            out.derived_vat_rate = max(rates)
        return

    # Otherwise pick the largest taxable amount's category as the
    # primary treatment (mixed-VAT bills are rare; pragmatic).
    subs_with_amount = [
        s for s in out.tax_subtotals
        if s.get("taxable_amount") is not None
    ]
    if not subs_with_amount:
        primary = out.tax_subtotals[0]
    else:
        primary = max(
            subs_with_amount,
            key=lambda s: s.get("taxable_amount") or Decimal(0),
        )
    cat_id = primary.get("category_id") or "S"
    out.derived_treatment = _TAX_CATEGORY_TO_TREATMENT.get(cat_id, "domestic")
    out.derived_vat_code = _TAX_CATEGORY_TO_VAT_CODE.get(cat_id, "T1")
    out.derived_vat_rate = primary.get("percent")


def _validate(out: ParsedPeppolInvoice) -> None:
    """Light-touch validation: flag missing fields the AP cycle gate
    depends on. The validation_gate (Wave 1 / D1) re-runs hard
    checks at approval time; this is just a hint to the operator
    that the inbound XML was incomplete."""
    if not out.invoice_id:
        out.warnings.append("missing_invoice_id")
    if not out.supplier_name:
        out.warnings.append("missing_supplier_name")
    if out.payable_amount is None:
        out.warnings.append("missing_payable_amount")
    if not out.currency:
        out.warnings.append("missing_currency")
    if (
        out.tax_inclusive_amount is not None
        and out.payable_amount is not None
        and abs(out.tax_inclusive_amount - out.payable_amount) > Decimal("0.01")
    ):
        out.warnings.append("payable_amount_mismatches_tax_inclusive_amount")
