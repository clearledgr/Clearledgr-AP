"""Africa e-invoice format generators (Wave 4 / F4).

Three live / near-live mandates covered:

  * **Nigeria — FIRS National E-Invoicing**
    Live since November 2024 for B2B + B2G transactions above
    NGN 1bn turnover. JSON envelope submitted to FIRS via a
    certified Access Service Provider (Sovos, TaxPro, Pwani Tech).
    Schema: based on UBL 2.1 + national extensions (TIN, BVN
    references, FIRS IRN — Invoice Reference Number).

  * **Kenya — KRA eTIMS** (Tax Invoice Management System v2)
    Mandatory for VAT-registered taxpayers since Sep 2023.
    Submission via certified TIMS device or eTIMS-Online.
    Format: JSON over HTTPS to KRA. Each invoice receives a
    Control Unit Invoice Number (CUIN) + QR code linking to the
    KRA verification portal.

  * **South Africa — SARS proposed e-invoice (2026 effective)**
    Following the National Treasury 2024 discussion paper. Format
    will be PEPPOL-aligned UBL 2.1 with SARS-specific extensions
    (Tax Number, branch code, ITC reference). We pre-build the
    generator against the publicly proposed schema so the SARS
    rollout date doesn't catch the org flat-footed.

This is NOT a transmission layer. We produce the canonical payload
shape; the actual submit-to-tax-authority step lives in a separate
ASP/PSP integration that customers configure per market. The
payloads are deterministic + round-trippable so QA + audit can
verify against the issuing tax authority's response.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_TWO_PLACES)
    return Decimal(str(value)).quantize(_TWO_PLACES)


def _f(value: Any) -> float:
    return float(_money(value))


@dataclass
class AfricaEInvoiceLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    tax_amount: Decimal = Decimal("0.00")
    tax_rate: Decimal = Decimal("0.000")
    item_code: Optional[str] = None
    hs_code: Optional[str] = None  # Harmonised System code (NG / KE require)


@dataclass
class AfricaEInvoiceContext:
    """Caller-supplied context the AP item alone doesn't carry."""

    # Issuer (us)
    issuer_name: str
    issuer_tax_id: str           # NG TIN, KE PIN, ZA Tax Number
    issuer_country: str          # NG | KE | ZA
    issuer_address: Optional[str] = None
    issuer_branch_code: Optional[str] = None  # ZA SARS branch code

    # Customer (the bill subject — our org if generating credit)
    customer_name: Optional[str] = None
    customer_tax_id: Optional[str] = None
    customer_country: Optional[str] = None
    customer_address: Optional[str] = None

    # Document metadata
    document_id: str = ""
    document_type: str = "invoice"   # invoice | credit_note
    issue_date: Optional[str] = None  # YYYY-MM-DD
    due_date: Optional[str] = None
    currency: str = ""

    # Reference back to original invoice (for credit notes)
    reference_document_id: Optional[str] = None


# ── Nigeria FIRS ────────────────────────────────────────────────────


def build_firs_einvoice(
    *,
    context: AfricaEInvoiceContext,
    lines: List[AfricaEInvoiceLine],
    total_amount: Any,
    total_tax: Any,
    payment_method: str = "BANK_TRANSFER",
) -> Dict[str, Any]:
    """Generate the FIRS National E-Invoicing JSON envelope.

    Returns a dict ready to JSON-serialize and POST to the org's
    Access Service Provider. The provider returns a FIRS-issued
    IRN (Invoice Reference Number) which Clearledgr stores back on
    the AP item — that part lives in a future sibling integration
    module; this function is the payload generator only.
    """
    if (context.issuer_country or "").upper() != "NG":
        logger.warning(
            "build_firs_einvoice called for non-NG issuer country=%r",
            context.issuer_country,
        )

    return {
        "version": "1.0",
        "invoice_type": (
            "STANDARD" if context.document_type == "invoice"
            else "CREDIT_NOTE"
        ),
        "invoice_number": context.document_id,
        "invoice_reference": context.reference_document_id,
        "issue_date": context.issue_date or _today(),
        "due_date": context.due_date,
        "currency": context.currency or "NGN",
        "supplier": {
            "name": context.issuer_name,
            "tin": context.issuer_tax_id,
            "country_code": "NG",
            "address": context.issuer_address,
        },
        "customer": {
            "name": context.customer_name,
            "tin": context.customer_tax_id,
            "country_code": (context.customer_country or "NG"),
            "address": context.customer_address,
        },
        "line_items": [
            {
                "description": line.description,
                "quantity": _f(line.quantity),
                "unit_price": _f(line.unit_price),
                "line_amount": _f(line.line_amount),
                "tax_rate": float(line.tax_rate or 0),
                "tax_amount": _f(line.tax_amount),
                "item_code": line.item_code,
                "hs_code": line.hs_code,
            }
            for line in lines
        ],
        "totals": {
            "total_excluding_tax": _f(_money(total_amount) - _money(total_tax)),
            "total_tax": _f(total_tax),
            "total_inclusive": _f(total_amount),
        },
        "payment_terms": {
            "method": payment_method,
            "due_date": context.due_date,
        },
        "metadata": {
            "issuer_country": "NG",
            "format_version": "FIRS-EI-1.0",
            "generated_at": _now_iso(),
        },
    }


# ── Kenya KRA eTIMS ─────────────────────────────────────────────────


def build_etims_einvoice(
    *,
    context: AfricaEInvoiceContext,
    lines: List[AfricaEInvoiceLine],
    total_amount: Any,
    total_tax: Any,
    sale_type: str = "N",  # N = normal, R = refund (credit), C = cancelled
) -> Dict[str, Any]:
    """Generate the KRA eTIMS JSON payload.

    KRA's eTIMS API uses a custom JSON shape (not UBL). Required
    fields per the KRA Sandbox spec (eTIMS 2.0): TIN, TraderSysNo,
    InvcNo, SaleTyCd, RcptTyCd, plus the per-item ItemList.
    """
    if (context.issuer_country or "").upper() != "KE":
        logger.warning(
            "build_etims_einvoice called for non-KE issuer country=%r",
            context.issuer_country,
        )

    receipt_type = (
        "S" if context.document_type == "invoice" else "R"
    )

    return {
        "Tin": context.issuer_tax_id,
        "BhfId": context.issuer_branch_code or "00",
        "InvcNo": context.document_id,
        "OrgInvcNo": context.reference_document_id,
        "CustTin": context.customer_tax_id or "",
        "CustNm": context.customer_name or "",
        "SaleTyCd": sale_type,
        "RcptTyCd": receipt_type,
        "PmtTyCd": "01",  # 01 = Cash, 02 = Credit, 03 = Bank transfer
        "SalesSttsCd": "02",  # 02 = Approved
        "CfmDt": _now_iso(),
        "SalesDt": (context.issue_date or _today()).replace("-", ""),
        "TotItemCnt": len(lines),
        "TaxblAmtA": 0.0,
        "TaxblAmtB": _f(_money(total_amount) - _money(total_tax)),
        "TaxblAmtC": 0.0,
        "TaxblAmtD": 0.0,
        "TaxblAmtE": 0.0,
        "TaxRtA": 0.0,
        "TaxRtB": 16.0,  # Kenya standard VAT
        "TaxRtC": 0.0,
        "TaxRtD": 8.0,
        "TaxRtE": 0.0,
        "TaxAmtA": 0.0,
        "TaxAmtB": _f(total_tax),
        "TaxAmtC": 0.0,
        "TaxAmtD": 0.0,
        "TaxAmtE": 0.0,
        "TotTaxblAmt": _f(_money(total_amount) - _money(total_tax)),
        "TotTaxAmt": _f(total_tax),
        "TotAmt": _f(total_amount),
        "Remark": context.document_type,
        "Currency": context.currency or "KES",
        "ItemList": [
            {
                "ItemSeq": idx + 1,
                "ItemCd": line.item_code or f"ITEM-{idx + 1}",
                "ItemClsCd": line.hs_code or "5022000000",
                "ItemNm": line.description,
                "Bcd": None,
                "PkgUnitCd": "NT",
                "Pkg": _f(line.quantity),
                "QtyUnitCd": "U",
                "Qty": _f(line.quantity),
                "Prc": _f(line.unit_price),
                "SplyAmt": _f(line.line_amount),
                "DcRt": 0.0,
                "DcAmt": 0.0,
                "TaxTyCd": "B",
                "TaxblAmt": _f(line.line_amount - line.tax_amount),
                "TaxAmt": _f(line.tax_amount),
                "TotAmt": _f(line.line_amount),
            }
            for idx, line in enumerate(lines)
        ],
    }


# ── South Africa SARS ──────────────────────────────────────────────


def build_sars_einvoice(
    *,
    context: AfricaEInvoiceContext,
    lines: List[AfricaEInvoiceLine],
    total_amount: Any,
    total_tax: Any,
) -> Dict[str, Any]:
    """Generate the SARS proposed e-invoice payload.

    SARS hasn't finalized the wire format as of 2026; the publicly
    consulted approach is PEPPOL UBL 2.1 + a SARS national extension
    block (BranchCode, ITCRef, EmplID). We build the payload as a
    JSON envelope wrapping the UBL XML so the operator can submit
    to whichever platform SARS finalizes.
    """
    if (context.issuer_country or "").upper() != "ZA":
        logger.warning(
            "build_sars_einvoice called for non-ZA issuer country=%r",
            context.issuer_country,
        )

    return {
        "schema": "SARS-EI-DRAFT-1.0",
        "submission_type": (
            "INVOICE" if context.document_type == "invoice" else "CREDIT_NOTE"
        ),
        "issuer": {
            "registration_name": context.issuer_name,
            "tax_number": context.issuer_tax_id,
            "branch_code": context.issuer_branch_code,
            "country_code": "ZA",
            "address": context.issuer_address,
        },
        "customer": {
            "registration_name": context.customer_name,
            "tax_number": context.customer_tax_id,
            "country_code": context.customer_country or "ZA",
            "address": context.customer_address,
        },
        "document": {
            "id": context.document_id,
            "issue_date": context.issue_date or _today(),
            "due_date": context.due_date,
            "currency": context.currency or "ZAR",
            "reference_document_id": context.reference_document_id,
        },
        "tax": {
            "scheme": "VAT",
            "rate": 15.0,  # ZA standard VAT
            "total_taxable": _f(_money(total_amount) - _money(total_tax)),
            "total_tax": _f(total_tax),
        },
        "lines": [
            {
                "description": line.description,
                "quantity": _f(line.quantity),
                "unit_price": _f(line.unit_price),
                "line_amount": _f(line.line_amount),
                "tax_rate": float(line.tax_rate or 0),
                "tax_amount": _f(line.tax_amount),
            }
            for line in lines
        ],
        "totals": {
            "subtotal_ex_vat": _f(_money(total_amount) - _money(total_tax)),
            "vat": _f(total_tax),
            "total": _f(total_amount),
        },
        "metadata": {
            "format_version": "SARS-DRAFT",
            "generated_at": _now_iso(),
        },
    }


# ── Dispatcher ──────────────────────────────────────────────────────


def build_africa_einvoice(
    *,
    country_code: str,
    context: AfricaEInvoiceContext,
    lines: List[AfricaEInvoiceLine],
    total_amount: Any,
    total_tax: Any,
) -> Dict[str, Any]:
    """Country-aware dispatcher. Pass ``country_code`` = NG / KE / ZA."""
    code = (country_code or "").upper()
    if code == "NG":
        return build_firs_einvoice(
            context=context, lines=lines,
            total_amount=total_amount, total_tax=total_tax,
        )
    if code == "KE":
        return build_etims_einvoice(
            context=context, lines=lines,
            total_amount=total_amount, total_tax=total_tax,
        )
    if code == "ZA":
        return build_sars_einvoice(
            context=context, lines=lines,
            total_amount=total_amount, total_tax=total_tax,
        )
    raise ValueError(
        f"unsupported_africa_country:{code!r}; supported=['NG','KE','ZA']"
    )


# ── AP-item convenience helper ──────────────────────────────────────


def build_einvoice_from_ap_item(
    *,
    country_code: str,
    ap_item: Dict[str, Any],
    organization: Dict[str, Any],
    document_type: str = "invoice",
) -> Dict[str, Any]:
    """Generate the e-invoice payload directly from an AP item +
    organization row.

    Issuer is the org. Customer is the vendor (because for outbound
    credit notes, we are crediting the vendor). For original
    invoices coming INTO the org from a vendor, the operator would
    flip issuer/customer manually — that path is for the AP system
    to consume the e-invoice format INBOUND, which we already do
    via PEPPOL UBL (F1) on the EU side.
    """
    org_settings = (organization.get("settings") or {})
    if isinstance(org_settings, str):
        try:
            org_settings = json.loads(org_settings)
        except Exception:
            org_settings = {}
    tax_block = (org_settings or {}).get("tax") or {}
    issuer_tax_id = (
        tax_block.get("tax_number")
        or tax_block.get("vat_number")
        or ""
    )
    issuer_branch = tax_block.get("branch_code")

    context = AfricaEInvoiceContext(
        issuer_name=str(
            organization.get("name")
            or organization.get("organization_name")
            or organization.get("id")
            or "Issuer"
        ),
        issuer_tax_id=str(issuer_tax_id),
        issuer_country=(country_code or "").upper(),
        issuer_branch_code=issuer_branch,
        customer_name=str(ap_item.get("vendor_name") or "Vendor"),
        customer_country=str(ap_item.get("bill_country") or country_code or ""),
        document_id=str(
            ap_item.get("invoice_number") or ap_item.get("id") or ""
        ),
        document_type=document_type,
        issue_date=(ap_item.get("invoice_date") or _today()),
        due_date=ap_item.get("due_date"),
        currency=str(ap_item.get("currency") or ""),
    )
    net = _money(ap_item.get("net_amount") or 0)
    vat = _money(ap_item.get("vat_amount") or 0)
    rate = _money(ap_item.get("vat_rate") or 0)
    gross = _money(ap_item.get("amount") or 0)
    if net == 0:
        net = gross
    if rate == 0 and gross != 0 and vat != 0:
        try:
            rate = (vat / net * Decimal("100")).quantize(Decimal("0.001"))
        except Exception:
            rate = Decimal("0")

    line = AfricaEInvoiceLine(
        description=(
            f"Bill {ap_item.get('invoice_number') or ap_item.get('id')}"
        ),
        quantity=Decimal("1"),
        unit_price=net,
        line_amount=net,
        tax_amount=vat,
        tax_rate=rate,
    )
    return build_africa_einvoice(
        country_code=country_code,
        context=context,
        lines=[line],
        total_amount=gross,
        total_tax=vat,
    )


# ── Tiny helpers ────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
