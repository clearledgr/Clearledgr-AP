"""ComplyAdvantage KYC provider adapter (Wave 3 / E1).

Wraps the ComplyAdvantage REST API for the four KYC checks Clearledgr
runs (company registry, sanctions, PEP, adverse media). The provider
sells one search endpoint that returns matches across all four lists
in a single call, with per-match list-membership flags; we split the
single response into four ``KYCCheckResult`` shapes via the same
underlying search call to amortize cost.

Reference: https://docs.complyadvantage.com/api/

Environment:
  ``COMPLYADVANTAGE_API_KEY``  — workspace API key (required).
  ``COMPLYADVANTAGE_BASE_URL`` — defaults to https://api.complyadvantage.com.

The adapter registers itself under ``"complyadvantage"`` so existing
``settings_json["onboarding"]["kyc_provider"] = "complyadvantage"``
configs route here.

Failure modes are explicit, not retried internally:
  * No API key configured           → ``status="error"``, error="api_key_missing"
  * 4xx response (bad search query) → ``status="error"``, error=f"http_{code}"
  * 5xx / timeout                   → ``status="error"``, error="upstream_unavailable"
The planner's existing retry-with-backoff handles the latter.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clearledgr.services.onboarding.kyc_provider import (
    KYCCheckResult,
    KYCProvider,
    register_kyc_provider,
)

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.complyadvantage.com"
_TIMEOUT_SECONDS = 30


# ── Helpers ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_match(types: List[str]) -> Dict[str, bool]:
    """ComplyAdvantage returns a ``types`` array per match — values
    like ``sanction``, ``warning``, ``pep``, ``pep-class-1``,
    ``adverse-media``. Roll up into the four categories Clearledgr
    cares about."""
    flat = " ".join(types).lower()
    return {
        "is_sanctions": "sanction" in flat,
        "is_pep": "pep" in flat,
        "is_adverse_media": "adverse-media" in flat or "adverse_media" in flat,
        "is_warning": "warning" in flat,
    }


def _normalize_matches(
    matches: List[Dict[str, Any]],
    *,
    keep: str,
) -> List[Dict[str, Any]]:
    """Filter the raw match list to only those of the requested
    category and trim the per-match payload to the fields the audit
    log actually needs (so we don't store kilobytes of unrelated
    fields)."""
    out: List[Dict[str, Any]] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        types = m.get("types") or []
        if not isinstance(types, list):
            continue
        flags = _classify_match([str(t) for t in types])
        if keep == "sanctions" and not flags["is_sanctions"]:
            continue
        if keep == "pep" and not flags["is_pep"]:
            continue
        if keep == "adverse_media" and not flags["is_adverse_media"]:
            continue
        # Keep stable identification + the original list types so the
        # operator review surface can render meaningful detail.
        doc = m.get("doc") or {}
        out.append({
            "match_id": m.get("id") or doc.get("id"),
            "name": doc.get("name"),
            "entity_type": doc.get("entity_type"),
            "match_score": m.get("match_score"),
            "types": types,
            "aka": doc.get("aka"),
            "fields": doc.get("fields"),
        })
    return out


def _categorize_status(matches: List[Dict[str, Any]]) -> str:
    return "hit" if matches else "clear"


# ── HTTP ────────────────────────────────────────────────────────────


async def _post_search(
    *,
    api_key: str,
    base_url: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Single-call ComplyAdvantage search. Raises on non-2xx."""
    from clearledgr.core.http_client import get_http_client

    client = get_http_client()
    url = f"{base_url.rstrip('/')}/searches"
    response = await client.post(
        url,
        params={"api_key": api_key},
        json=payload,
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json() or {}


# ── Adapter ─────────────────────────────────────────────────────────


class ComplyAdvantageProvider(KYCProvider):
    """ComplyAdvantage REST adapter.

    Each Clearledgr-side method runs a single search and filters the
    response to the relevant match category. ``company_registry_lookup``
    + ``resolve_ubo`` aren't ComplyAdvantage products — they fall back
    to ``inconclusive`` so the planner routes those to a different
    provider or to manual review.
    """

    provider = "complyadvantage"

    def __init__(
        self,
        *,
        organization_id: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.organization_id = organization_id
        self._api_key = (
            api_key
            or os.getenv("COMPLYADVANTAGE_API_KEY", "").strip()
            or None
        )
        self._base_url = (
            base_url
            or os.getenv("COMPLYADVANTAGE_BASE_URL", "").strip()
            or _DEFAULT_BASE_URL
        )

    def _missing_key(self, check_type: str) -> KYCCheckResult:
        return KYCCheckResult(
            status="error",
            check_type=check_type,
            provider=self.provider,
            error="api_key_missing",
            checked_at=_now_iso(),
        )

    async def _search(
        self,
        *,
        legal_name: str,
        country: str,
        check_type: str,
        types_filter: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self._api_key:
            return None
        payload: Dict[str, Any] = {
            "search_term": legal_name,
            "client_ref": (
                f"clearledgr:{self.organization_id}:{legal_name}"
                if self.organization_id else f"clearledgr:{legal_name}"
            ),
            "fuzziness": 0.6,
            "filters": {
                "country_codes": [country.upper()] if country else [],
                "types": types_filter or [
                    "sanction", "warning", "pep", "adverse-media",
                ],
            },
        }
        try:
            return await _post_search(
                api_key=self._api_key,
                base_url=self._base_url,
                payload=payload,
            )
        except Exception as exc:
            logger.warning(
                "complyadvantage %s search failed for %r: %s",
                check_type, legal_name, exc,
            )
            return {"_error": str(exc), "_status_code": getattr(
                getattr(exc, "response", None), "status_code", None,
            )}

    def _result_from_search(
        self,
        *,
        search: Optional[Dict[str, Any]],
        check_type: str,
        keep: str,
    ) -> KYCCheckResult:
        if search is None:
            return self._missing_key(check_type)
        if isinstance(search, dict) and "_error" in search:
            sc = search.get("_status_code")
            if isinstance(sc, int) and 400 <= sc < 500:
                return KYCCheckResult(
                    status="error",
                    check_type=check_type,
                    provider=self.provider,
                    error=f"http_{sc}",
                    checked_at=_now_iso(),
                )
            return KYCCheckResult(
                status="error",
                check_type=check_type,
                provider=self.provider,
                error="upstream_unavailable",
                checked_at=_now_iso(),
            )
        content = (search.get("content") or {}) if isinstance(search, dict) else {}
        data = content.get("data") if isinstance(content, dict) else None
        if not isinstance(data, dict):
            data = {}
        raw_matches = data.get("hits") or []
        if not isinstance(raw_matches, list):
            raw_matches = []
        matches = _normalize_matches(raw_matches, keep=keep)
        return KYCCheckResult(
            status=_categorize_status(matches),
            check_type=check_type,
            provider=self.provider,
            provider_reference=str(data.get("id") or "") or None,
            matches=matches,
            evidence={
                "total_hits": len(raw_matches),
                "filtered_hits": len(matches),
                "search_term": data.get("search_term"),
            },
            raw_payload=data,
            checked_at=_now_iso(),
        )

    async def company_registry_lookup(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        # ComplyAdvantage does NOT operate a company registry product
        # — fall through to inconclusive so the planner routes to a
        # different provider (Trulioo, Companies House direct, etc.)
        # or to manual verification.
        return KYCCheckResult(
            status="inconclusive",
            check_type="company_registry",
            provider=self.provider,
            error="company_registry_not_supported_by_provider",
            checked_at=_now_iso(),
        )

    async def sanctions_screen(
        self,
        *,
        legal_name: str,
        country: str,
        aliases: Optional[List[str]] = None,
    ) -> KYCCheckResult:
        search = await self._search(
            legal_name=legal_name,
            country=country,
            check_type="sanctions",
            types_filter=["sanction", "warning"],
        )
        return self._result_from_search(
            search=search, check_type="sanctions", keep="sanctions",
        )

    async def pep_check(
        self,
        *,
        legal_name: str,
        country: str,
        beneficial_owners: Optional[List[Dict[str, Any]]] = None,
    ) -> KYCCheckResult:
        search = await self._search(
            legal_name=legal_name,
            country=country,
            check_type="pep",
            types_filter=["pep"],
        )
        return self._result_from_search(
            search=search, check_type="pep", keep="pep",
        )

    async def adverse_media_screen(
        self,
        *,
        legal_name: str,
        country: str,
    ) -> KYCCheckResult:
        search = await self._search(
            legal_name=legal_name,
            country=country,
            check_type="adverse_media",
            types_filter=["adverse-media"],
        )
        return self._result_from_search(
            search=search, check_type="adverse_media", keep="adverse_media",
        )

    async def resolve_ubo(
        self,
        *,
        legal_name: str,
        country: str,
        registration_number: Optional[str] = None,
    ) -> KYCCheckResult:
        return KYCCheckResult(
            status="inconclusive",
            check_type="ubo",
            provider=self.provider,
            error="ubo_not_supported_by_provider",
            checked_at=_now_iso(),
        )


# Adapter registration (import side-effect). Workspaces opt in via
# settings_json["onboarding"]["kyc_provider"] = "complyadvantage".
register_kyc_provider(
    "complyadvantage",
    lambda organization_id=None: ComplyAdvantageProvider(
        organization_id=organization_id,
    ),
)
