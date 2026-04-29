"""Multi-attribute vendor matching (Wave 5 / G2).

When a bill arrives claiming to be from "Acme Corp", confirming
identity by name alone is a fraud surface — the #1 vector in
pre-Clearledgr AP fraud is BEC vendors that spell the name right
and slip in a different IBAN. This module scores the bill against
the stored vendor profile across five attributes:

  * **Name** (fuzzy match — Jaro-Winkler-like ratio)
  * **VAT ID** (exact, normalized — strips spaces / dots / dashes)
  * **IBAN** (exact, normalized)
  * **Sender domain** (for Gmail-arrived bills)
  * **Address fingerprint** (city + postal zone — strong domestic
    discriminator)

Each attribute returns ``{matched, score, expected, observed}`` so
the operator sees on the approval card EXACTLY which attributes
agreed and which differed. The aggregate confidence is the weighted
average; the highest-risk flag (``iban_mismatch``) wins for
overall_status because IBAN swaps are the canonical BEC payload.

This is pure compute. No DB writes; no external API calls. The
caller (typically the validation gate) persists the result onto
``ap_items.metadata`` and feeds it into the risk surface.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Normalization helpers ──────────────────────────────────────────


_VAT_NORMALIZE_RE = re.compile(r"[\s\-.]")
_IBAN_NORMALIZE_RE = re.compile(r"\s+")


def _norm_vat(value: Optional[str]) -> str:
    if not value:
        return ""
    return _VAT_NORMALIZE_RE.sub("", str(value)).upper().strip()


def _norm_iban(value: Optional[str]) -> str:
    if not value:
        return ""
    return _IBAN_NORMALIZE_RE.sub("", str(value)).upper().strip()


def _norm_text(value: Optional[str]) -> str:
    if not value:
        return ""
    out = str(value).lower().strip()
    # Strip company-suffix noise that complicates name comparison.
    for suffix in (
        " gmbh", " ag", " sarl", " sas", " sa", " bv", " ltd",
        " limited", " inc.", " inc", " llc", " plc", " corp.",
        " corp", " co.", " co", " kft", " s.r.l.", " s.r.l",
        " spa", " s.p.a.", " s.p.a", " ab", " oy", " oyj", " s.l.",
    ):
        if out.endswith(suffix):
            out = out[: -len(suffix)].strip()
            break
    out = re.sub(r"[^a-z0-9 ]", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _norm_domain(email_or_domain: Optional[str]) -> str:
    if not email_or_domain:
        return ""
    s = str(email_or_domain).lower().strip()
    if "@" in s:
        s = s.rsplit("@", 1)[-1]
    return s.strip().strip(".")


def _norm_postal(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", str(value)).upper().strip()


# ── Fuzzy name matching ────────────────────────────────────────────


def _name_similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity for two vendor names. Uses normalized
    token-set comparison — robust to suffix differences and word
    reordering."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union) if union else 0.0
    # Length ratio penalises very different lengths (e.g. "Acme" vs
    # "Acme Industries Trading Holdings International").
    len_ratio = min(len(a), len(b)) / max(len(a), len(b))
    return round(0.7 * jaccard + 0.3 * len_ratio, 3)


# ── Per-attribute matchers ─────────────────────────────────────────


def _match_name(
    bill_name: Optional[str], vendor_name: Optional[str],
) -> Dict[str, Any]:
    a = _norm_text(bill_name)
    b = _norm_text(vendor_name)
    score = _name_similarity(a, b)
    matched = score >= 0.85
    return {
        "attribute": "name",
        "matched": matched,
        "score": score,
        "expected": vendor_name,
        "observed": bill_name,
    }


def _match_vat(
    bill_vat: Optional[str], vendor_vat: Optional[str],
) -> Dict[str, Any]:
    a = _norm_vat(bill_vat)
    b = _norm_vat(vendor_vat)
    if not a and not b:
        # Neither side has a VAT id — return matched=None style:
        # no useful signal, score=None so the aggregator skips it.
        return {
            "attribute": "vat_id",
            "matched": None,
            "score": None,
            "expected": vendor_vat,
            "observed": bill_vat,
            "note": "no_vat_id_on_either_side",
        }
    if not a or not b:
        return {
            "attribute": "vat_id",
            "matched": False,
            "score": 0.0,
            "expected": vendor_vat,
            "observed": bill_vat,
            "note": (
                "vat_id_missing_on_bill" if not a
                else "vat_id_missing_on_vendor_profile"
            ),
        }
    return {
        "attribute": "vat_id",
        "matched": a == b,
        "score": 1.0 if a == b else 0.0,
        "expected": vendor_vat,
        "observed": bill_vat,
    }


def _match_iban(
    bill_iban: Optional[str], vendor_iban: Optional[str],
) -> Dict[str, Any]:
    a = _norm_iban(bill_iban)
    b = _norm_iban(vendor_iban)
    if not a and not b:
        return {
            "attribute": "iban",
            "matched": None,
            "score": None,
            "expected": vendor_iban,
            "observed": bill_iban,
            "note": "no_iban_on_either_side",
        }
    if not a or not b:
        return {
            "attribute": "iban",
            "matched": False,
            "score": 0.0,
            "expected": vendor_iban,
            "observed": bill_iban,
            "note": (
                "iban_missing_on_bill" if not a
                else "iban_missing_on_vendor_profile"
            ),
        }
    return {
        "attribute": "iban",
        "matched": a == b,
        "score": 1.0 if a == b else 0.0,
        "expected": vendor_iban,
        "observed": bill_iban,
        "note": (
            None if a == b else "iban_mismatch_high_fraud_risk"
        ),
    }


def _match_domain(
    bill_sender: Optional[str],
    vendor_domains: Optional[List[str]],
) -> Dict[str, Any]:
    if not vendor_domains:
        return {
            "attribute": "sender_domain",
            "matched": None,
            "score": None,
            "expected": vendor_domains,
            "observed": bill_sender,
            "note": "no_vendor_domains_recorded",
        }
    bill_domain = _norm_domain(bill_sender)
    if not bill_domain:
        return {
            "attribute": "sender_domain",
            "matched": False,
            "score": 0.0,
            "expected": vendor_domains,
            "observed": bill_sender,
            "note": "no_sender_on_bill",
        }
    matched_domains = [
        d for d in vendor_domains
        if _norm_domain(d) and (
            _norm_domain(d) == bill_domain
            or bill_domain.endswith("." + _norm_domain(d))
        )
    ]
    return {
        "attribute": "sender_domain",
        "matched": bool(matched_domains),
        "score": 1.0 if matched_domains else 0.0,
        "expected": vendor_domains,
        "observed": bill_sender,
        "note": (
            None if matched_domains
            else "sender_domain_not_in_known_list"
        ),
    }


def _match_address(
    bill_address: Optional[Dict[str, Any]],
    vendor_address: Optional[str],
    vendor_postal: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare city + postal zone if the bill carries them. Bill
    address arrives as a structured dict (per the PEPPOL/InvoiceData
    extraction); vendor address is a free-text registered_address
    column. Match the postal zone strictly + fuzzy-match the
    address text."""
    bill_postal = _norm_postal((bill_address or {}).get("postal_zone"))
    bill_city = _norm_text((bill_address or {}).get("city"))
    if not bill_postal and not bill_city:
        return {
            "attribute": "address",
            "matched": None,
            "score": None,
            "expected": vendor_address,
            "observed": bill_address,
            "note": "no_address_on_bill",
        }
    if not vendor_address and not vendor_postal:
        return {
            "attribute": "address",
            "matched": None,
            "score": None,
            "expected": vendor_address,
            "observed": bill_address,
            "note": "no_address_on_vendor_profile",
        }
    norm_vendor = _norm_text(vendor_address)
    if vendor_postal:
        # Strict postal match wins.
        return {
            "attribute": "address",
            "matched": _norm_postal(vendor_postal) == bill_postal,
            "score": 1.0 if _norm_postal(vendor_postal) == bill_postal else 0.0,
            "expected": vendor_address,
            "observed": bill_address,
        }
    # Fuzzy text match of the city against the address.
    score = (
        1.0 if (bill_city and bill_city in norm_vendor)
        else 0.0
    )
    return {
        "attribute": "address",
        "matched": score >= 0.99,
        "score": score,
        "expected": vendor_address,
        "observed": bill_address,
    }


# ── Aggregator ─────────────────────────────────────────────────────


_DEFAULT_WEIGHTS: Dict[str, float] = {
    "name": 0.25,
    "vat_id": 0.30,
    "iban": 0.25,
    "sender_domain": 0.10,
    "address": 0.10,
}


@dataclass
class VendorMatchResult:
    vendor_name: str
    overall_status: str       # ok | suspicious | mismatch | profile_missing
    confidence: float          # 0..1
    attributes: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_name": self.vendor_name,
            "overall_status": self.overall_status,
            "confidence": self.confidence,
            "attributes": list(self.attributes),
            "flags": list(self.flags),
        }


def match_bill_against_vendor_profile(
    *,
    bill_vendor_name: Optional[str],
    bill_vat_id: Optional[str] = None,
    bill_iban: Optional[str] = None,
    bill_sender: Optional[str] = None,
    bill_address: Optional[Dict[str, Any]] = None,
    vendor_profile: Optional[Dict[str, Any]] = None,
) -> VendorMatchResult:
    """Score the bill against a vendor profile dict.

    ``vendor_profile`` should be the row returned by
    ``db.get_vendor_profile`` — fields read: vendor_name,
    vendor_aliases (JSON list), sender_domains (JSON list),
    registered_address, and any encrypted bank details surfaced via
    a typed accessor (caller pre-decrypts).

    Returns a :class:`VendorMatchResult` ready to persist on
    ``ap_items.metadata['vendor_match']``.
    """
    if vendor_profile is None:
        return VendorMatchResult(
            vendor_name=bill_vendor_name or "",
            overall_status="profile_missing",
            confidence=0.0,
            flags=["vendor_profile_missing"],
        )

    name_match = _match_name(
        bill_vendor_name, vendor_profile.get("vendor_name"),
    )
    vat_match = _match_vat(
        bill_vat_id, vendor_profile.get("vat_number"),
    )
    iban_match = _match_iban(
        bill_iban, vendor_profile.get("expected_iban"),
    )
    domain_match = _match_domain(
        bill_sender, vendor_profile.get("sender_domains"),
    )
    address_match = _match_address(
        bill_address,
        vendor_profile.get("registered_address"),
    )

    attributes = [name_match, vat_match, iban_match, domain_match, address_match]

    # Weighted confidence (skip attributes with score=None — neither
    # side carried the data).
    weighted_sum = 0.0
    weight_total = 0.0
    for attr in attributes:
        score = attr.get("score")
        if score is None:
            continue
        weight = _DEFAULT_WEIGHTS.get(attr["attribute"], 0.0)
        weighted_sum += weight * float(score)
        weight_total += weight
    confidence = (
        round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0
    )

    flags: List[str] = []
    if iban_match.get("matched") is False and iban_match.get("score") == 0.0:
        flags.append("iban_mismatch")
    if vat_match.get("matched") is False and vat_match.get("score") == 0.0:
        flags.append("vat_mismatch")
    if name_match.get("score", 0) < 0.85:
        flags.append("name_low_similarity")

    if "iban_mismatch" in flags:
        overall_status = "mismatch"
    elif "vat_mismatch" in flags:
        overall_status = "mismatch"
    elif confidence >= 0.85:
        overall_status = "ok"
    elif confidence >= 0.55:
        overall_status = "suspicious"
    else:
        overall_status = "mismatch"

    return VendorMatchResult(
        vendor_name=bill_vendor_name or "",
        overall_status=overall_status,
        confidence=confidence,
        attributes=attributes,
        flags=flags,
    )


# ── DB-backed entry point ──────────────────────────────────────────


def evaluate_ap_item_vendor_match(
    db,
    *,
    organization_id: str,
    ap_item_id: str,
) -> Optional[VendorMatchResult]:
    """Pull the AP item + vendor profile and score them.

    Reads bill attributes from the AP item's metadata (where the
    inbound parsers — Gmail extraction, PEPPOL UBL, ERP intake —
    deposit the canonical fields)."""
    item = db.get_ap_item(ap_item_id)
    if item is None or item.get("organization_id") != organization_id:
        return None
    raw_meta = item.get("metadata")
    if isinstance(raw_meta, str):
        try:
            import json as _json
            raw_meta = _json.loads(raw_meta) if raw_meta else {}
        except Exception:
            raw_meta = {}
    meta = raw_meta if isinstance(raw_meta, dict) else {}

    bill_vat = (
        meta.get("supplier_vat_id")
        or meta.get("vat_number")
        or meta.get("vat_id")
    )
    bill_iban = None
    bank_details = meta.get("bank_details")
    if isinstance(bank_details, dict):
        bill_iban = bank_details.get("iban")
    bill_address = None
    addr = meta.get("supplier_address") or meta.get("address")
    if isinstance(addr, dict):
        bill_address = addr
    elif meta.get("supplier_country") or meta.get("supplier_city"):
        bill_address = {
            "country": meta.get("supplier_country"),
            "city": meta.get("supplier_city"),
            "postal_zone": meta.get("supplier_postal_zone"),
        }
    bill_sender = item.get("sender")

    vendor_profile = None
    vendor_name = item.get("vendor_name")
    if vendor_name:
        try:
            vendor_profile = db.get_vendor_profile(
                organization_id, vendor_name,
            )
        except Exception:
            vendor_profile = None

    return match_bill_against_vendor_profile(
        bill_vendor_name=vendor_name,
        bill_vat_id=bill_vat,
        bill_iban=bill_iban,
        bill_sender=bill_sender,
        bill_address=bill_address,
        vendor_profile=vendor_profile,
    )
