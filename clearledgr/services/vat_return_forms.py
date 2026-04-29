"""Country-specific VAT return form mappings (Wave 4 / F3).

The E2 :func:`compute_vat_return_boxes` produces the canonical
9-box rollup (HMRC-shaped). Each EU member state has its own
return form with different box numbering + groupings. This module
maps the canonical rollup onto the country-specific shape so the
operator's accountant can transcribe (or import) directly.

Coverage:

  * **GB** — HMRC VAT Return (boxes 1-9). Canonical = native; this
    is the identity mapping.
  * **DE** — UStVA (Umsatzsteuer-Voranmeldung). Key codes:
      Kz41 / Kz81  Tax-free intra-community supplies (sales side)
      Kz89         VAT due on intra-community acquisitions
      Kz67         VAT on reverse-charge services received (Sec. 13b)
      Kz66         Input VAT (deductible)
      Kz83         Net VAT payable / refund due
  * **NL** — BTW-aangifte. Rubrieken:
      4a    Acquisitions from EU (taxable)
      4b    Acquisitions from non-EU
      5a    Total output VAT (boxes 1+2+3+4 cumulative)
      5b    Input VAT
      5c    Net VAT due
  * **FR** — CA3. Lines:
      08    Domestic taxable purchases (operations imposables)
      17    VAT on intra-EU acquisitions (auto-liquidation)
      19    Input VAT to deduct
      28    Net VAT due

The mappings are pragmatic — every country's form has dozens of
boxes for niche cases. We map the AP-side ones and leave the
sales-side at zero so the operator finishes from their AR data.

Each mapping returns:

  {
      "jurisdiction": "DE",
      "form_name": "UStVA",
      "fields": [
          {"code": "...", "label": "...", "amount": <float>, "source_box": "..."},
          ...
      ],
      "summary": {"net_vat_payable": <float>, "currency": "EUR"},
      "canonical_boxes": {...},   # the original E2 rollup, for audit
  }
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


_SUPPORTED_JURISDICTIONS = ("GB", "DE", "NL", "FR")


def _f(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def map_to_country_form(
    canonical_boxes: Dict[str, Any],
    *,
    jurisdiction: str,
    currency: str = "GBP",
) -> Dict[str, Any]:
    """Produce a country-specific VAT return shape from a canonical
    9-box rollup.

    ``canonical_boxes`` is the dict returned by
    :func:`clearledgr.services.vat_return.compute_vat_return_boxes`
    (or the equivalent fields on a persisted ``vat_returns`` row).
    """
    jur = (jurisdiction or "").strip().upper() or "GB"
    mapper = _MAPPERS.get(jur)
    if mapper is None:
        raise ValueError(
            f"unsupported_jurisdiction:{jur!r}; "
            f"supported={list(_SUPPORTED_JURISDICTIONS)}"
        )
    return mapper(canonical_boxes, currency=currency)


# ── Per-country mappers ────────────────────────────────────────────


def _map_gb(boxes: Dict[str, Any], *, currency: str) -> Dict[str, Any]:
    """HMRC VAT Return — canonical 9-box layout, identity mapping."""
    fields = [
        {"code": "1",
         "label": "VAT due on sales and other outputs",
         "amount": _f(boxes.get("box1_vat_due_on_sales")),
         "source_box": "box1_vat_due_on_sales"},
        {"code": "2",
         "label": "VAT due on EU acquisitions of goods",
         "amount": _f(boxes.get("box2_vat_due_on_acquisitions")),
         "source_box": "box2_vat_due_on_acquisitions"},
        {"code": "3",
         "label": "Total VAT due (1+2)",
         "amount": _f(boxes.get("box3_total_vat_due")),
         "source_box": "box3_total_vat_due"},
        {"code": "4",
         "label": "VAT reclaimed on purchases and other inputs",
         "amount": _f(boxes.get("box4_vat_reclaimed")),
         "source_box": "box4_vat_reclaimed"},
        {"code": "5",
         "label": "Net VAT to pay or reclaim (3-4)",
         "amount": _f(boxes.get("box5_net_vat_payable")),
         "source_box": "box5_net_vat_payable"},
        {"code": "6",
         "label": "Total value of sales and other outputs ex VAT",
         "amount": _f(boxes.get("box6_total_sales_ex_vat")),
         "source_box": "box6_total_sales_ex_vat"},
        {"code": "7",
         "label": "Total value of purchases and other inputs ex VAT",
         "amount": _f(boxes.get("box7_total_purchases_ex_vat")),
         "source_box": "box7_total_purchases_ex_vat"},
        {"code": "8",
         "label": "Total value of supplies of goods to EU",
         "amount": _f(boxes.get("box8_total_eu_sales")),
         "source_box": "box8_total_eu_sales"},
        {"code": "9",
         "label": "Total value of acquisitions of goods from EU",
         "amount": _f(boxes.get("box9_total_eu_purchases")),
         "source_box": "box9_total_eu_purchases"},
    ]
    return {
        "jurisdiction": "GB",
        "form_name": "VAT Return",
        "fields": fields,
        "summary": {
            "net_vat_payable": _f(boxes.get("box5_net_vat_payable")),
            "currency": currency or "GBP",
        },
        "canonical_boxes": canonical_boxes_subset(boxes),
    }


def _map_de(boxes: Dict[str, Any], *, currency: str) -> Dict[str, Any]:
    """Germany UStVA — Umsatzsteuer-Voranmeldung.

    Reverse-charge acquisitions from EU services land in Kz67
    (Leistungen nach §13b UStG). Input-VAT reclaim is Kz66 (Vorsteuer
    aus Rechnungen anderer Unternehmer)."""
    box1 = _f(boxes.get("box1_vat_due_on_sales"))
    box4 = _f(boxes.get("box4_vat_reclaimed"))
    box7 = _f(boxes.get("box7_total_purchases_ex_vat"))
    box9 = _f(boxes.get("box9_total_eu_purchases"))

    # VAT due on sales (output side incl. RC self-assessed) goes
    # into Kz67 if it's RC-derived and Kz81 otherwise. Our AP-only
    # projection puts all of box1 into Kz67 since AR is out of scope.
    fields = [
        {"code": "Kz41",
         "label": "Innergemeinschaftliche Lieferungen (sales — out of AP scope)",
         "amount": _f(boxes.get("box8_total_eu_sales")),
         "source_box": "box8_total_eu_sales"},
        {"code": "Kz89",
         "label": "Innergemeinschaftliche Erwerbe — Steuer",
         "amount": _f(boxes.get("box2_vat_due_on_acquisitions")),
         "source_box": "box2_vat_due_on_acquisitions"},
        {"code": "Kz93",
         "label": "Innergemeinschaftliche Erwerbe — Bemessungsgrundlage",
         "amount": box9,
         "source_box": "box9_total_eu_purchases"},
        {"code": "Kz67",
         "label": "Steuer aus §13b Leistungen (reverse charge — services)",
         "amount": box1,
         "source_box": "box1_vat_due_on_sales"},
        {"code": "Kz66",
         "label": "Vorsteuer (input VAT reclaim)",
         "amount": box4,
         "source_box": "box4_vat_reclaimed"},
        {"code": "Kz62",
         "label": "Bemessungsgrundlage Eingangsumsätze (purchases ex VAT)",
         "amount": box7,
         "source_box": "box7_total_purchases_ex_vat"},
        {"code": "Kz83",
         "label": "Verbleibende Umsatzsteuer-Vorauszahlung (net VAT payable)",
         "amount": _f(boxes.get("box5_net_vat_payable")),
         "source_box": "box5_net_vat_payable"},
    ]
    return {
        "jurisdiction": "DE",
        "form_name": "UStVA",
        "fields": fields,
        "summary": {
            "net_vat_payable": _f(boxes.get("box5_net_vat_payable")),
            "currency": currency or "EUR",
        },
        "canonical_boxes": dict(canonical_boxes_subset(boxes)),
    }


def _map_nl(boxes: Dict[str, Any], *, currency: str) -> Dict[str, Any]:
    """Netherlands BTW-aangifte (Belastingdienst Aangifte
    Omzetbelasting). Rubrieken 4a/4b/5a/5b/5c are the AP-relevant
    boxes."""
    box1 = _f(boxes.get("box1_vat_due_on_sales"))
    box2 = _f(boxes.get("box2_vat_due_on_acquisitions"))
    box4 = _f(boxes.get("box4_vat_reclaimed"))
    box5 = _f(boxes.get("box5_net_vat_payable"))
    box7 = _f(boxes.get("box7_total_purchases_ex_vat"))
    box9 = _f(boxes.get("box9_total_eu_purchases"))

    fields = [
        {"code": "4a_belast",
         "label": "Leveringen/diensten uit EU (taxable acquisitions ex VAT)",
         "amount": box9,
         "source_box": "box9_total_eu_purchases"},
        {"code": "4a_btw",
         "label": "BTW op acquisitions uit EU (RC self-assessed output)",
         "amount": box1 + box2,
         "source_box": "box1_vat_due_on_sales+box2_vat_due_on_acquisitions"},
        {"code": "5a",
         "label": "Verschuldigde BTW (total output VAT)",
         "amount": box1 + box2,
         "source_box": "box1_vat_due_on_sales+box2_vat_due_on_acquisitions"},
        {"code": "5b",
         "label": "Voorbelasting (input VAT to deduct)",
         "amount": box4,
         "source_box": "box4_vat_reclaimed"},
        {"code": "5c",
         "label": "Te betalen / terug te vragen (net VAT due)",
         "amount": box5,
         "source_box": "box5_net_vat_payable"},
        {"code": "purchases_ex_vat",
         "label": "Total purchases ex VAT (informational)",
         "amount": box7,
         "source_box": "box7_total_purchases_ex_vat"},
    ]
    return {
        "jurisdiction": "NL",
        "form_name": "BTW-aangifte",
        "fields": fields,
        "summary": {
            "net_vat_payable": box5,
            "currency": currency or "EUR",
        },
        "canonical_boxes": dict(canonical_boxes_subset(boxes)),
    }


def _map_fr(boxes: Dict[str, Any], *, currency: str) -> Dict[str, Any]:
    """France CA3. Lines mapped to AP-side equivalents."""
    box1 = _f(boxes.get("box1_vat_due_on_sales"))
    box4 = _f(boxes.get("box4_vat_reclaimed"))
    box7 = _f(boxes.get("box7_total_purchases_ex_vat"))
    box9 = _f(boxes.get("box9_total_eu_purchases"))
    box5 = _f(boxes.get("box5_net_vat_payable"))

    fields = [
        {"code": "08_base",
         "label": "Acquisitions intracommunautaires (base HT)",
         "amount": box9,
         "source_box": "box9_total_eu_purchases"},
        {"code": "17",
         "label": "TVA sur acquisitions intracommunautaires (auto-liquidation)",
         "amount": box1 + _f(boxes.get("box2_vat_due_on_acquisitions")),
         "source_box": "box1_vat_due_on_sales+box2_vat_due_on_acquisitions"},
        {"code": "19",
         "label": "TVA déductible sur autres biens et services",
         "amount": box4,
         "source_box": "box4_vat_reclaimed"},
        {"code": "20",
         "label": "Total achats HT (informatif)",
         "amount": box7,
         "source_box": "box7_total_purchases_ex_vat"},
        {"code": "28",
         "label": "TVA nette due / crédit",
         "amount": box5,
         "source_box": "box5_net_vat_payable"},
    ]
    return {
        "jurisdiction": "FR",
        "form_name": "CA3",
        "fields": fields,
        "summary": {
            "net_vat_payable": box5,
            "currency": currency or "EUR",
        },
        "canonical_boxes": dict(canonical_boxes_subset(boxes)),
    }


def canonical_boxes_subset(boxes: Dict[str, Any]) -> Dict[str, Any]:
    """Echo the canonical box values into the country form payload —
    useful for audit ('show me what the original 9-box was')."""
    keep_keys = (
        "box1_vat_due_on_sales",
        "box2_vat_due_on_acquisitions",
        "box3_total_vat_due",
        "box4_vat_reclaimed",
        "box5_net_vat_payable",
        "box6_total_sales_ex_vat",
        "box7_total_purchases_ex_vat",
        "box8_total_eu_sales",
        "box9_total_eu_purchases",
    )
    return {k: _f(boxes.get(k)) for k in keep_keys}


_MAPPERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "GB": _map_gb,
    "DE": _map_de,
    "NL": _map_nl,
    "FR": _map_fr,
}


def supported_jurisdictions() -> List[str]:
    return list(_SUPPORTED_JURISDICTIONS)
