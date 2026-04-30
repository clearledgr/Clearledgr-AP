"""Tax compliance service — Europe/Africa jurisdiction-specific tax reporting.

Supports:
- VAT number validation by country format (EU, UK, Nigeria, Kenya)
- Reverse charge detection (B2B intra-EU)
- WHT (withholding tax) rate lookup by country
- Annual vendor payment totals for tax threshold monitoring
- Tax summary report by jurisdiction

All tax rules are configurable per-org via settings_json["tax_compliance"].
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VAT number format patterns by country prefix
# ---------------------------------------------------------------------------

VAT_PATTERNS: Dict[str, re.Pattern] = {
    # EU member states
    "AT": re.compile(r"^ATU\d{8}$"),
    "BE": re.compile(r"^BE0?\d{9,10}$"),
    "BG": re.compile(r"^BG\d{9,10}$"),
    "CY": re.compile(r"^CY\d{8}[A-Z]$"),
    "CZ": re.compile(r"^CZ\d{8,10}$"),
    "DE": re.compile(r"^DE\d{9}$"),
    "DK": re.compile(r"^DK\d{8}$"),
    "EE": re.compile(r"^EE\d{9}$"),
    "ES": re.compile(r"^ES[A-Z0-9]\d{7}[A-Z0-9]$"),
    "FI": re.compile(r"^FI\d{8}$"),
    "FR": re.compile(r"^FR[A-Z0-9]{2}\d{9}$"),
    "GR": re.compile(r"^EL\d{9}$"),
    "HR": re.compile(r"^HR\d{11}$"),
    "HU": re.compile(r"^HU\d{8}$"),
    "IE": re.compile(r"^IE\d[A-Z0-9+*]\d{5}[A-Z]$"),
    "IT": re.compile(r"^IT\d{11}$"),
    "LT": re.compile(r"^LT\d{9,12}$"),
    "LU": re.compile(r"^LU\d{8}$"),
    "LV": re.compile(r"^LV\d{11}$"),
    "MT": re.compile(r"^MT\d{8}$"),
    "NL": re.compile(r"^NL\d{9}B\d{2}$"),
    "PL": re.compile(r"^PL\d{10}$"),
    "PT": re.compile(r"^PT\d{9}$"),
    "RO": re.compile(r"^RO\d{2,10}$"),
    "SE": re.compile(r"^SE\d{12}$"),
    "SI": re.compile(r"^SI\d{8}$"),
    "SK": re.compile(r"^SK\d{10}$"),
    # UK
    "GB": re.compile(r"^GB\d{9}$|^GB\d{12}$|^GBGD\d{3}$|^GBHA\d{3}$"),
    # Africa
    "NG": re.compile(r"^NG\d{12}$"),  # Nigeria TIN (12 digits after prefix, dashes stripped)
    "KE": re.compile(r"^KE[A-Z]\d{9}[A-Z]$|^[A-Z]\d{9}[A-Z]$"),  # Kenya KRA PIN
    "GH": re.compile(r"^GH[A-Z0-9]{10,15}$|^[A-Z0-9]{10,15}$"),  # Ghana TIN
    "ZA": re.compile(r"^ZA\d{10}$|^\d{10}$"),  # South Africa
}

# Standard VAT rates by country (%)
STANDARD_VAT_RATES: Dict[str, float] = {
    "AT": 20.0, "BE": 21.0, "BG": 20.0, "CY": 19.0, "CZ": 21.0,
    "DE": 19.0, "DK": 25.0, "EE": 22.0, "ES": 21.0, "FI": 24.0,
    "FR": 20.0, "GR": 24.0, "HR": 25.0, "HU": 27.0, "IE": 23.0,
    "IT": 22.0, "LT": 21.0, "LU": 17.0, "LV": 21.0, "MT": 18.0,
    "NL": 21.0, "PL": 23.0, "PT": 23.0, "RO": 19.0, "SE": 25.0,
    "SI": 22.0, "SK": 20.0, "GB": 20.0,
    "NG": 7.5,   # Nigeria VAT
    "KE": 16.0,  # Kenya VAT
    "GH": 15.0,  # Ghana VAT (standard + NHIL + GetFund)
    "ZA": 15.0,  # South Africa VAT
}

# WHT rates by country (default rate for services)
WHT_RATES: Dict[str, float] = {
    "NG": 10.0,   # Nigeria WHT on services
    "KE": 5.0,    # Kenya WHT on services
    "GH": 7.5,    # Ghana WHT
    "ZA": 15.0,   # South Africa WHT (non-resident)
}

# EU member state codes (for intra-community detection)
EU_COUNTRIES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_tax_id(tax_id: str, country_code: str = "") -> Dict[str, Any]:
    """Validate a tax ID / VAT number format.

    If no country_code provided, tries to detect from the prefix.
    Returns {valid, country, format_matched, normalized}.
    """
    if not tax_id:
        return {"valid": False, "reason": "empty"}

    cleaned = re.sub(r'[\s\-.]', '', tax_id).upper()

    # Detect country from prefix if not provided
    if not country_code:
        for prefix in sorted(VAT_PATTERNS.keys(), key=len, reverse=True):
            if cleaned.startswith(prefix):
                country_code = prefix
                break

    if not country_code:
        return {
            "valid": False,
            "reason": "unknown_country",
            "normalized": cleaned,
        }

    pattern = VAT_PATTERNS.get(country_code.upper())
    if not pattern:
        return {
            "valid": False,
            "reason": "unsupported_country",
            "country": country_code,
            "normalized": cleaned,
        }

    # Ensure prefix is present
    if not cleaned.startswith(country_code.upper()):
        cleaned = country_code.upper() + cleaned

    matched = bool(pattern.match(cleaned))
    return {
        "valid": matched,
        "country": country_code.upper(),
        "format_matched": matched,
        "normalized": cleaned,
        "reason": "" if matched else "format_mismatch",
    }


def detect_reverse_charge(
    buyer_country: str,
    seller_country: str,
    seller_has_vat: bool = True,
) -> Dict[str, Any]:
    """Detect if reverse charge VAT applies (B2B intra-EU).

    Reverse charge applies when:
    - Both buyer and seller are in different EU member states
    - Both are VAT-registered businesses
    """
    buyer = buyer_country.upper()
    seller = seller_country.upper()
    buyer_eu = buyer in EU_COUNTRIES
    seller_eu = seller in EU_COUNTRIES

    if buyer_eu and seller_eu and buyer != seller and seller_has_vat:
        return {
            "reverse_charge": True,
            "reason": "intra_eu_b2b",
            "buyer_country": buyer,
            "seller_country": seller,
            "note": "VAT should not be charged by seller. Buyer self-assesses.",
        }

    return {"reverse_charge": False, "buyer_country": buyer, "seller_country": seller}


def get_wht_rate(country_code: str) -> Optional[float]:
    """Get the default withholding tax rate for a country."""
    return WHT_RATES.get(country_code.upper())


def get_vat_rate(country_code: str) -> Optional[float]:
    """Get the standard VAT rate for a country."""
    return STANDARD_VAT_RATES.get(country_code.upper())


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TaxComplianceService:
    """Tax compliance reporting for a single tenant."""

    def __init__(self, organization_id: str = "default") -> None:
        self.organization_id = organization_id
        from clearledgr.core.database import get_db
        self.db = get_db()

    def get_vendor_payment_totals(
        self, period_start: str, period_end: str,
    ) -> List[Dict[str, Any]]:
        """Annual payment totals per vendor for tax threshold monitoring.

        Returns vendors with total amounts paid in the period.
        """
        sql = (
            "SELECT vendor_name, currency, SUM(amount) as total, COUNT(*) as invoice_count "
            "FROM ap_items "
            "WHERE organization_id = %s "
            "AND state IN ('posted_to_erp', 'closed') "
            "AND created_at >= %s AND created_at < %s "
            "AND amount IS NOT NULL "
            "GROUP BY vendor_name, currency "
            "ORDER BY total DESC"
        )
        try:
            self.db.initialize()
            with self.db.connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (self.organization_id, period_start, period_end))
                rows = [dict(r) for r in cur.fetchall()]

            results = []
            for row in rows:
                vendor = row.get("vendor_name") or "Unknown"
                total = float(row.get("total") or 0)
                currency = row.get("currency") or "USD"
                count = int(row.get("invoice_count") or 0)

                # Look up vendor tax ID from profile metadata
                tax_id = ""
                try:
                    profile = self.db.get_vendor_profile(self.organization_id, vendor)
                    if profile:
                        meta = profile.get("metadata") or {}
                        if isinstance(meta, str):
                            import json
                            meta = json.loads(meta)
                        tax_id = meta.get("erp_tax_id") or ""
                except Exception:
                    pass

                results.append({
                    "vendor_name": vendor,
                    "currency": currency,
                    "total_paid": round(total, 2),
                    "invoice_count": count,
                    "tax_id": tax_id,
                    "tax_id_valid": validate_tax_id(tax_id)["valid"] if tax_id else None,
                })

            return results
        except Exception as exc:
            logger.warning("[TaxCompliance] get_vendor_payment_totals failed: %s", exc)
            return []

    def generate_tax_summary(
        self,
        year: int = 0,
        buyer_country: str = "",
    ) -> Dict[str, Any]:
        """Generate a tax compliance summary for the year.

        Includes: vendor totals, VAT validation status, reverse charge flags,
        WHT applicability.
        """
        if not year:
            year = datetime.now(timezone.utc).year

        period_start = f"{year}-01-01"
        period_end = f"{year + 1}-01-01"

        vendor_totals = self.get_vendor_payment_totals(period_start, period_end)

        # Enrich with tax analysis
        vendors_missing_tax_id = []
        vendors_invalid_tax_id = []
        reverse_charge_applicable = []
        wht_applicable = []

        for v in vendor_totals:
            tax_id = v.get("tax_id", "")
            if not tax_id:
                vendors_missing_tax_id.append(v["vendor_name"])
            elif not v.get("tax_id_valid"):
                vendors_invalid_tax_id.append(v["vendor_name"])

            # Detect reverse charge if buyer country is known
            if buyer_country and tax_id:
                validation = validate_tax_id(tax_id)
                seller_country = validation.get("country", "")
                if seller_country:
                    rc = detect_reverse_charge(buyer_country, seller_country)
                    if rc.get("reverse_charge"):
                        reverse_charge_applicable.append({
                            "vendor_name": v["vendor_name"],
                            "seller_country": seller_country,
                            "total_paid": v["total_paid"],
                        })

            # Check WHT applicability by vendor tax ID country
            if tax_id:
                validation = validate_tax_id(tax_id)
                vendor_country = validation.get("country", "")
                wht_rate = get_wht_rate(vendor_country)
                if wht_rate:
                    wht_applicable.append({
                        "vendor_name": v["vendor_name"],
                        "country": vendor_country,
                        "wht_rate_pct": wht_rate,
                        "total_paid": v["total_paid"],
                        "estimated_wht": round(v["total_paid"] * wht_rate / 100, 2),
                    })

        return {
            "organization_id": self.organization_id,
            "year": year,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "buyer_country": buyer_country,
            "vendor_count": len(vendor_totals),
            "vendor_totals": vendor_totals,
            "vendors_missing_tax_id": vendors_missing_tax_id,
            "vendors_invalid_tax_id": vendors_invalid_tax_id,
            "missing_tax_id_count": len(vendors_missing_tax_id),
            "invalid_tax_id_count": len(vendors_invalid_tax_id),
            "reverse_charge_applicable": reverse_charge_applicable,
            "wht_applicable": wht_applicable,
        }


def get_tax_compliance_service(organization_id: str = "default") -> TaxComplianceService:
    return TaxComplianceService(organization_id=organization_id)
