"""Business registry vendor verification via OpenCorporates.

Module 4 spec line 158: "Verification: agent attempts auto-verification
on creation (IBAN check, business registry lookup, prior payment
match). Surfaces unverified vendors for human review."

OpenCorporates v0.4 is the default registry source — free for low
volume, paid tier covers most enterprise customers, broad jurisdiction
coverage (EU, US, UK, CA, AU, plus dozens of others). Customers can
override with Companies House (UK-only) by setting
``REGISTRY_PROVIDER=companies_house`` and ``COMPANIES_HOUSE_KEY``.

Returns a result envelope:
  { "status": "verified" | "not_found" | "ambiguous" | "error",
    "registry": "opencorporates",
    "company_number": "...",
    "jurisdiction": "...",
    "company_name": "...",
    "incorporation_date": "...",
    "active": bool,
    "match_score": 0.0-1.0,
    "raw": {...},   # full registry payload for audit
    "error": "..." | None }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def verify_vendor_registration(
    *,
    company_name: str,
    registration_number: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> Dict[str, Any]:
    """Top-level dispatcher. Picks provider via REGISTRY_PROVIDER env."""
    provider = os.getenv("REGISTRY_PROVIDER", "opencorporates").strip().lower()
    if provider == "companies_house":
        return _verify_via_companies_house(
            company_name=company_name,
            registration_number=registration_number,
        )
    return _verify_via_opencorporates(
        company_name=company_name,
        registration_number=registration_number,
        jurisdiction=jurisdiction,
    )


def _verify_via_opencorporates(
    *,
    company_name: str,
    registration_number: Optional[str] = None,
    jurisdiction: Optional[str] = None,
) -> Dict[str, Any]:
    """OpenCorporates v0.4 lookup.

    If we have a registration_number + jurisdiction, hit the company
    endpoint directly (deterministic). Otherwise search by name and
    return the best match (with score < 1.0 so the operator knows it
    needs human ack).
    """
    try:
        import httpx
    except ImportError:
        return {"status": "error", "registry": "opencorporates",
                "error": "httpx not installed."}

    api_key = os.getenv("OPENCORPORATES_API_KEY", "").strip()
    base = "https://api.opencorporates.com/v0.4"
    params: Dict[str, Any] = {}
    if api_key:
        params["api_token"] = api_key

    # Direct lookup when we have the canonical identifier.
    if registration_number and jurisdiction:
        url = f"{base}/companies/{jurisdiction.lower()}/{registration_number}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, params=params)
        except Exception as exc:
            return {"status": "error", "registry": "opencorporates", "error": str(exc)}
        if resp.status_code == 404:
            return {"status": "not_found", "registry": "opencorporates"}
        if resp.status_code != 200:
            return {"status": "error", "registry": "opencorporates",
                    "error": f"HTTP {resp.status_code}"}
        try:
            data = resp.json()
            company = ((data.get("results") or {}).get("company")) or {}
        except Exception as exc:
            return {"status": "error", "registry": "opencorporates", "error": str(exc)}
        return _shape_company(company, match_score=1.0)

    # Name search fallback.
    if not company_name:
        return {"status": "error", "registry": "opencorporates",
                "error": "company_name required when registration_number is missing"}
    url = f"{base}/companies/search"
    params["q"] = company_name
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction.lower()
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params=params)
    except Exception as exc:
        return {"status": "error", "registry": "opencorporates", "error": str(exc)}
    if resp.status_code != 200:
        return {"status": "error", "registry": "opencorporates",
                "error": f"HTTP {resp.status_code}"}
    try:
        data = resp.json()
        items = ((data.get("results") or {}).get("companies")) or []
    except Exception as exc:
        return {"status": "error", "registry": "opencorporates", "error": str(exc)}

    if not items:
        return {"status": "not_found", "registry": "opencorporates"}

    # Pick the best match by Levenshtein-style ratio of names.
    target = company_name.strip().lower()
    best = None
    best_score = 0.0
    for item in items:
        company = item.get("company") or {}
        candidate_name = str(company.get("name") or "").strip().lower()
        score = _name_similarity(target, candidate_name)
        if score > best_score:
            best_score = score
            best = company

    if not best or best_score < 0.6:
        return {"status": "ambiguous", "registry": "opencorporates",
                "match_score": round(best_score, 2),
                "candidates": [
                    {"name": (i.get("company") or {}).get("name"),
                     "company_number": (i.get("company") or {}).get("company_number"),
                     "jurisdiction": (i.get("company") or {}).get("jurisdiction_code")}
                    for i in items[:5]
                ]}

    return _shape_company(best, match_score=round(best_score, 2))


def _shape_company(company: Dict[str, Any], match_score: float) -> Dict[str, Any]:
    return {
        "status": "verified",
        "registry": "opencorporates",
        "company_number": company.get("company_number"),
        "jurisdiction": company.get("jurisdiction_code"),
        "company_name": company.get("name"),
        "incorporation_date": company.get("incorporation_date"),
        "dissolution_date": company.get("dissolution_date"),
        "active": (str(company.get("current_status") or "").lower() == "active"),
        "match_score": match_score,
        "raw": {k: company.get(k) for k in (
            "name", "company_number", "jurisdiction_code", "incorporation_date",
            "dissolution_date", "current_status", "company_type", "registry_url",
        )},
    }


def _name_similarity(a: str, b: str) -> float:
    """Character-bigram Jaccard similarity. Fast, no deps, good enough
    for "is this the same vendor?" matching with normal noise tolerance.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    bg_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bg_b = {b[i:i + 2] for i in range(len(b) - 1)}
    inter = bg_a & bg_b
    union = bg_a | bg_b
    return len(inter) / len(union) if union else 0.0


def _verify_via_companies_house(
    *,
    company_name: str,
    registration_number: Optional[str] = None,
) -> Dict[str, Any]:
    """UK-only. Customer must set COMPANIES_HOUSE_KEY."""
    try:
        import httpx
    except ImportError:
        return {"status": "error", "registry": "companies_house",
                "error": "httpx not installed."}
    api_key = os.getenv("COMPANIES_HOUSE_KEY", "").strip()
    if not api_key:
        return {"status": "error", "registry": "companies_house",
                "error": "COMPANIES_HOUSE_KEY not configured."}
    auth = (api_key, "")  # CH uses HTTP Basic with empty password
    if registration_number:
        url = f"https://api.company-information.service.gov.uk/company/{registration_number}"
        try:
            with httpx.Client(timeout=10.0, auth=auth) as client:
                resp = client.get(url)
        except Exception as exc:
            return {"status": "error", "registry": "companies_house", "error": str(exc)}
        if resp.status_code == 404:
            return {"status": "not_found", "registry": "companies_house"}
        if resp.status_code != 200:
            return {"status": "error", "registry": "companies_house",
                    "error": f"HTTP {resp.status_code}"}
        try:
            data = resp.json()
        except Exception as exc:
            return {"status": "error", "registry": "companies_house", "error": str(exc)}
        return {
            "status": "verified",
            "registry": "companies_house",
            "company_number": data.get("company_number"),
            "jurisdiction": "gb",
            "company_name": data.get("company_name"),
            "incorporation_date": data.get("date_of_creation"),
            "active": str(data.get("company_status") or "").lower() == "active",
            "match_score": 1.0,
            "raw": data,
        }

    # Name search
    url = "https://api.company-information.service.gov.uk/search/companies"
    try:
        with httpx.Client(timeout=10.0, auth=auth) as client:
            resp = client.get(url, params={"q": company_name})
    except Exception as exc:
        return {"status": "error", "registry": "companies_house", "error": str(exc)}
    if resp.status_code != 200:
        return {"status": "error", "registry": "companies_house",
                "error": f"HTTP {resp.status_code}"}
    try:
        data = resp.json()
        items = data.get("items") or []
    except Exception as exc:
        return {"status": "error", "registry": "companies_house", "error": str(exc)}
    if not items:
        return {"status": "not_found", "registry": "companies_house"}
    target = company_name.strip().lower()
    best = max(items, key=lambda c: _name_similarity(target, str(c.get("title") or "").strip().lower()))
    score = _name_similarity(target, str(best.get("title") or "").strip().lower())
    if score < 0.6:
        return {"status": "ambiguous", "registry": "companies_house",
                "match_score": round(score, 2),
                "candidates": [
                    {"name": c.get("title"), "company_number": c.get("company_number")}
                    for c in items[:5]
                ]}
    return {
        "status": "verified",
        "registry": "companies_house",
        "company_number": best.get("company_number"),
        "jurisdiction": "gb",
        "company_name": best.get("title"),
        "incorporation_date": best.get("date_of_creation"),
        "active": str(best.get("company_status") or "").lower() == "active",
        "match_score": round(score, 2),
        "raw": best,
    }
