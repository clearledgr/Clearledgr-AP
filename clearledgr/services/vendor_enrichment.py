"""Vendor enrichment service — DESIGN_THESIS §3.

Auto-populate vendor KYC fields from Companies House and HMRC VAT register.

Stateless: takes inputs, calls external APIs, returns a dict of enriched
fields. Stores results in the vendor profile via ``update_vendor_kyc``.
Never blocks onboarding — all lookups are best-effort.

Companies House API:
  - Search: GET /search/companies?q={name}  (free, no auth required)
  - Direct: GET /company/{number}           (free, API key recommended)

HMRC VAT lookup:
  - GET /organisations/vat/check-vat-number/lookup/{vat_number}  (free)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from clearledgr.core.http_client import get_http_client

logger = logging.getLogger(__name__)

_COMPANIES_HOUSE_BASE = "https://api.company-information.service.gov.uk"
_HMRC_VAT_BASE = "https://api.service.hmrc.gov.uk"
_TIMEOUT = 10.0  # seconds per request


# ---------------------------------------------------------------------- #
# Companies House                                                         #
# ---------------------------------------------------------------------- #


async def _companies_house_search(
    name: str,
    *,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Search Companies House by company name, return the top match."""
    url = f"{_COMPANIES_HOUSE_BASE}/search/companies"
    params = {"q": name, "items_per_page": "1"}
    headers: Dict[str, str] = {"Accept": "application/json"}

    try:
        client = get_http_client()
        if api_key:
            resp = await client.get(
                url, params=params, headers=headers, auth=(api_key, "")
            )
        else:
            resp = await client.get(url, params=params, headers=headers, timeout=_TIMEOUT)

        if resp.status_code != 200:
            logger.warning(
                "[vendor_enrichment] Companies House search returned %s for %r",
                resp.status_code, name,
            )
            return None

        data = resp.json()
        items = data.get("items") or []
        if not items:
            logger.info(
                "[vendor_enrichment] Companies House search: no results for %r",
                name,
            )
            return None

        return items[0]

    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "[vendor_enrichment] Companies House search error for %r: %s",
            name, exc,
        )
        return None


async def _companies_house_by_number(
    company_number: str,
    *,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a specific company by registration number."""
    url = f"{_COMPANIES_HOUSE_BASE}/company/{company_number}"
    headers: Dict[str, str] = {"Accept": "application/json"}

    try:
        client = get_http_client()
        if api_key:
            resp = await client.get(url, headers=headers, auth=(api_key, ""))
        else:
            resp = await client.get(url, headers=headers, timeout=_TIMEOUT)

        if resp.status_code != 200:
            logger.warning(
                "[vendor_enrichment] Companies House company/%s returned %s",
                company_number, resp.status_code,
            )
            return None

        return resp.json()

    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "[vendor_enrichment] Companies House company/%s error: %s",
            company_number, exc,
        )
        return None


async def _companies_house_officers(
    company_number: str,
    *,
    api_key: Optional[str] = None,
) -> List[str]:
    """Fetch active officers (directors) for a company."""
    url = f"{_COMPANIES_HOUSE_BASE}/company/{company_number}/officers"
    headers: Dict[str, str] = {"Accept": "application/json"}

    try:
        client = get_http_client()
        if api_key:
            resp = await client.get(url, headers=headers, auth=(api_key, ""))
        else:
            resp = await client.get(url, headers=headers, timeout=_TIMEOUT)

        if resp.status_code != 200:
            return []

        data = resp.json()
        items = data.get("items") or []
        # Only include active officers (no resigned_on date).
        return [
            item.get("name", "")
            for item in items
            if item.get("name") and not item.get("resigned_on")
        ]

    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "[vendor_enrichment] Companies House officers error for %s: %s",
            company_number, exc,
        )
        return []


def _format_ch_address(addr: Optional[Dict[str, Any]]) -> Optional[str]:
    """Flatten a Companies House address object into a single string."""
    if not addr or not isinstance(addr, dict):
        return None
    parts = [
        addr.get("premises"),
        addr.get("address_line_1"),
        addr.get("address_line_2"),
        addr.get("locality"),
        addr.get("region"),
        addr.get("postal_code"),
        addr.get("country"),
    ]
    return ", ".join(p.strip() for p in parts if p and str(p).strip())


async def lookup_companies_house(
    vendor_name: str,
    registration_number: Optional[str] = None,
) -> Dict[str, Any]:
    """Query Companies House and return enriched fields.

    If ``registration_number`` is provided, fetch directly by number.
    Otherwise, search by name and take the top match.

    Returns a dict with keys matching the vendor profile KYC columns:
      company_name, company_number, registered_address,
      company_status, date_of_creation, director_names
    """
    api_key = os.getenv("COMPANIES_HOUSE_API_KEY")
    result: Dict[str, Any] = {"source": "companies_house"}

    company_data: Optional[Dict[str, Any]] = None
    company_number: Optional[str] = None

    if registration_number:
        # Direct lookup by number — most precise.
        company_data = await _companies_house_by_number(
            registration_number, api_key=api_key
        )
        company_number = registration_number
    else:
        # Search by name, take top result.
        search_hit = await _companies_house_search(vendor_name, api_key=api_key)
        if search_hit:
            company_number = search_hit.get("company_number")
            # The search result has summary data; fetch full company for
            # complete address and officers.
            if company_number:
                company_data = await _companies_house_by_number(
                    company_number, api_key=api_key
                )
            if not company_data:
                # Fall back to search-result fields.
                company_data = search_hit

    if not company_data:
        return result

    result["company_name"] = company_data.get("company_name") or company_data.get("title")
    result["company_number"] = company_data.get("company_number") or company_number
    result["company_status"] = company_data.get("company_status")
    result["date_of_creation"] = company_data.get("date_of_creation")

    # Address — full company profile uses "registered_office_address",
    # search results use "address".
    addr = company_data.get("registered_office_address") or company_data.get("address")
    formatted = _format_ch_address(addr)
    if formatted:
        result["registered_address"] = formatted

    # Officers (directors).
    if company_number:
        officers = await _companies_house_officers(company_number, api_key=api_key)
        if officers:
            result["director_names"] = officers

    result["fields_populated"] = [
        k for k in ("company_name", "company_number", "registered_address",
                     "company_status", "date_of_creation", "director_names")
        if k in result and result[k]
    ]

    return result


# ---------------------------------------------------------------------- #
# HMRC VAT register                                                       #
# ---------------------------------------------------------------------- #


async def lookup_hmrc_vat(vat_number: str) -> Dict[str, Any]:
    """Query HMRC VAT register for a UK VAT number.

    Returns a dict with validated VAT details:
      target_name, target_address, processing_date, vat_number
    """
    result: Dict[str, Any] = {"source": "hmrc_vat"}

    # Normalise: strip "GB" prefix and spaces.
    cleaned = vat_number.strip().upper().replace(" ", "")
    if cleaned.startswith("GB"):
        cleaned = cleaned[2:]

    if not cleaned:
        return result

    url = f"{_HMRC_VAT_BASE}/organisations/vat/check-vat-number/lookup/{cleaned}"
    headers = {"Accept": "application/json"}

    try:
        client = get_http_client()
        resp = await client.get(url, headers=headers, timeout=_TIMEOUT)

        if resp.status_code != 200:
            logger.warning(
                "[vendor_enrichment] HMRC VAT lookup returned %s for %s",
                resp.status_code, cleaned,
            )
            return result

        data = resp.json()
        target = data.get("target") or {}

        result["vat_number"] = cleaned
        result["target_name"] = target.get("name")
        result["processing_date"] = data.get("processingDate")

        # HMRC address is a dict with line1..line5 + postcode + countryCode.
        addr = target.get("address") or {}
        addr_parts = [
            addr.get(f"line{i}") for i in range(1, 6)
        ] + [addr.get("postcode"), addr.get("countryCode")]
        formatted = ", ".join(
            p.strip() for p in addr_parts if p and str(p).strip()
        )
        if formatted:
            result["target_address"] = formatted

        result["fields_populated"] = [
            k for k in ("target_name", "target_address", "processing_date", "vat_number")
            if k in result and result[k]
        ]

    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.warning(
            "[vendor_enrichment] HMRC VAT lookup error for %s: %s",
            cleaned, exc,
        )

    return result


# ---------------------------------------------------------------------- #
# Main enrichment entry point                                              #
# ---------------------------------------------------------------------- #


async def enrich_vendor(
    vendor_name: str,
    registration_number: Optional[str] = None,
    vat_number: Optional[str] = None,
    *,
    organization_id: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Enrich a vendor from public registries and optionally persist.

    Calls Companies House and (if ``vat_number`` is provided) HMRC VAT
    in parallel. Returns a combined dict of all enriched fields.

    If ``organization_id`` is provided and ``persist`` is True, the
    enriched fields are written to the vendor profile and an audit
    event is emitted.

    Never raises — all external errors are caught and logged. Returns
    partial results if one source fails.
    """
    import asyncio

    enriched: Dict[str, Any] = {
        "vendor_name": vendor_name,
        "sources": [],
    }

    # Fire lookups in parallel.
    tasks = [lookup_companies_house(vendor_name, registration_number)]
    if vat_number:
        tasks.append(lookup_hmrc_vat(vat_number))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process Companies House result.
    ch_result = results[0] if len(results) > 0 else None
    if isinstance(ch_result, dict) and ch_result.get("fields_populated"):
        enriched["companies_house"] = ch_result
        enriched["sources"].append("companies_house")
    elif isinstance(ch_result, Exception):
        logger.warning(
            "[vendor_enrichment] Companies House lookup raised: %s", ch_result
        )

    # Process HMRC VAT result.
    if vat_number and len(results) > 1:
        vat_result = results[1]
        if isinstance(vat_result, dict) and vat_result.get("fields_populated"):
            enriched["hmrc_vat"] = vat_result
            enriched["sources"].append("hmrc_vat")
        elif isinstance(vat_result, Exception):
            logger.warning(
                "[vendor_enrichment] HMRC VAT lookup raised: %s", vat_result
            )

    # Persist to vendor profile if we have an org context.
    if persist and organization_id and enriched["sources"]:
        await _persist_enrichment(organization_id, vendor_name, enriched)

    return enriched


async def _persist_enrichment(
    organization_id: str,
    vendor_name: str,
    enriched: Dict[str, Any],
) -> None:
    """Write enriched fields to the vendor profile and emit audit event."""
    try:
        from clearledgr.core.database import get_db

        db = get_db()

        # Build the KYC patch from enrichment results.
        patch: Dict[str, Any] = {}

        ch = enriched.get("companies_house") or {}
        if ch.get("company_number"):
            patch["registration_number"] = ch["company_number"]
        if ch.get("registered_address"):
            patch["registered_address"] = ch["registered_address"]
        if ch.get("director_names"):
            patch["director_names"] = ch["director_names"]

        vat = enriched.get("hmrc_vat") or {}
        if vat.get("vat_number"):
            patch["vat_number"] = vat["vat_number"]
        # If HMRC returned an address and we don't have one from
        # Companies House, use the HMRC address.
        if vat.get("target_address") and "registered_address" not in patch:
            patch["registered_address"] = vat["target_address"]

        if not patch:
            return

        # Update via the KYC update method — respects field whitelist.
        db.update_vendor_kyc(
            organization_id,
            vendor_name,
            patch=patch,
            actor_id="vendor_enrichment",
        )

        # Also store the full enrichment response in profile metadata
        # for audit/debugging.
        profile = db.get_vendor_profile(organization_id, vendor_name)
        if profile:
            meta = profile.get("metadata") or {}
            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            meta["last_enrichment"] = {
                "sources": enriched.get("sources", []),
                "companies_house": {
                    k: v for k, v in ch.items()
                    if k not in ("fields_populated", "source")
                } if ch else None,
                "hmrc_vat": {
                    k: v for k, v in vat.items()
                    if k not in ("fields_populated", "source")
                } if vat else None,
            }
            db.upsert_vendor_profile(
                organization_id, vendor_name, metadata=meta
            )

        # Audit event.
        db.append_audit_event(
            {
                "ap_item_id": "",
                "event_type": "vendor_enriched",
                "actor_type": "agent",
                "actor_id": "vendor_enrichment",
                "reason": (
                    f"Vendor {vendor_name} enriched from "
                    f"{', '.join(enriched.get('sources', []))}"
                ),
                "metadata": {
                    "vendor_name": vendor_name,
                    "sources": enriched.get("sources", []),
                    "fields_populated": (
                        (ch.get("fields_populated") or [])
                        + (vat.get("fields_populated") or [])
                    ),
                },
                "organization_id": organization_id,
                "source": "vendor_enrichment",
            }
        )

        logger.info(
            "[vendor_enrichment] persisted enrichment for %s/%s from %s",
            organization_id, vendor_name, enriched.get("sources"),
        )

    except Exception as exc:
        # Never block onboarding on enrichment persistence failures.
        logger.warning(
            "[vendor_enrichment] persist failed for %s/%s: %s",
            organization_id, vendor_name, exc,
        )
