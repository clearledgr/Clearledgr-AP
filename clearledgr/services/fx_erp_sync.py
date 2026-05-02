"""FX rates pull from ERP — Module 9 spec line 298.

Each ERP connector exposes FX rates differently. This service is the
unified entrypoint the workspace UI hits; it dispatches per ERP type
and returns a normalised result envelope so the SPA doesn't have to
know which ERP is connected.

Connector status (2026-05-01):
  - QuickBooks: shipped here. Uses /v3/company/{realm_id}/exchangerate
    against the org's functional currency for every currency that
    appears on open AP items.
  - Xero, NetSuite, SAP: stubbed with ``not_supported``. Each requires
    its own auth path + endpoint shape; tracked as a follow-up so the
    UI surfaces an actionable "manual entry only" hint rather than a
    generic 500. Implementing them is roughly 0.5-1 day per connector.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def sync_fx_rates_from_erp(db, organization_id: str, *, actor: str) -> Dict[str, Any]:
    """Top-level dispatcher — returns the result envelope the SPA renders."""
    org = db.get_organization(organization_id) or {}
    settings = org.get("settings_json") or org.get("settings") or {}
    if isinstance(settings, str):
        try:
            import json as _json
            settings = _json.loads(settings)
        except Exception:
            settings = {}
    functional_currency = str(settings.get("functional_currency") or settings.get("base_currency") or "USD").upper()

    # Currencies in use on open AP items — this is what we actually
    # need rates for. Closed/posted items already locked their rate.
    needed = _currencies_on_open_ap_items(db, organization_id)
    needed.discard(functional_currency)

    conns = []
    if hasattr(db, "get_erp_connections"):
        try:
            conns = db.get_erp_connections(organization_id) or []
        except Exception:
            conns = []
    primary = next((c for c in conns if c.get("is_active", 1)), None) or (conns[0] if conns else None)

    if not primary:
        return {
            "status": "no_erp_connected",
            "message": "Connect an ERP first; FX sync needs a source.",
            "rates_synced": 0,
            "currencies": sorted(needed),
        }

    erp_type = str(primary.get("erp_type") or "").strip().lower()

    if erp_type == "quickbooks":
        return _sync_from_quickbooks(
            db, organization_id, primary, functional_currency, needed, actor=actor,
        )
    if erp_type == "xero":
        return _sync_from_xero(
            db, organization_id, primary, functional_currency, needed, actor=actor,
        )
    if erp_type == "netsuite":
        return _sync_from_netsuite(
            db, organization_id, primary, functional_currency, needed, actor=actor,
        )
    if erp_type == "sap":
        return _sync_from_sap(
            db, organization_id, primary, functional_currency, needed, actor=actor,
        )
    return {
        "status": "unknown_erp",
        "erp_type": erp_type,
        "message": "Unknown ERP type; FX sync skipped.",
        "rates_synced": 0,
    }


def _currencies_on_open_ap_items(db, organization_id: str) -> Set[str]:
    """Return the set of distinct ISO-4217 currency codes on open AP items."""
    if not hasattr(db, "list_ap_items"):
        return set()
    try:
        items = db.list_ap_items(organization_id, limit=5000) or []
    except Exception:
        return set()
    open_states = {
        "received", "validated", "needs_info", "needs_approval",
        "pending_approval", "approved", "ready_to_post",
    }
    out: Set[str] = set()
    for item in items:
        state = str(item.get("state") or "").strip().lower()
        if state not in open_states:
            continue
        cur = str(item.get("currency") or "").strip().upper()
        if cur and len(cur) == 3:
            out.add(cur)
    return out


def _sync_from_quickbooks(
    db,
    organization_id: str,
    conn: Dict[str, Any],
    functional_currency: str,
    needed: Set[str],
    *,
    actor: str,
) -> Dict[str, Any]:
    """Pull rates from QuickBooks Online's /exchangerate endpoint.

    Endpoint reference:
      https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/exchangerate

    QB returns the rate for the source currency as of a given date
    against the company's home currency. We call once per source
    currency and upsert each as source='erp'.
    """
    realm_id = str(conn.get("realm_id") or "").strip()
    if not realm_id:
        return {
            "status": "missing_credentials",
            "message": "QuickBooks connection has no realm_id; reconnect to refresh.",
            "rates_synced": 0,
        }

    if not needed:
        return {
            "status": "no_currencies_needed",
            "message": f"No open AP items in non-functional currencies. Functional: {functional_currency}.",
            "rates_synced": 0,
        }

    # Lazy import to avoid pulling httpx at module-load time.
    try:
        import httpx
    except ImportError:
        return {"status": "httpx_unavailable", "rates_synced": 0}

    access_token = _qb_access_token(db, organization_id)
    if not access_token:
        return {
            "status": "missing_credentials",
            "message": "QuickBooks access token unavailable. Reconnect to refresh.",
            "rates_synced": 0,
        }

    base_url = str(conn.get("base_url") or "https://quickbooks.api.intuit.com").rstrip("/")
    today = datetime.now(timezone.utc).date().isoformat()
    results: List[Dict[str, Any]] = []
    synced = 0

    for source_currency in sorted(needed):
        url = f"{base_url}/v3/company/{realm_id}/exchangerate"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    url,
                    params={"sourcecurrencycode": source_currency, "asofdate": today},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
        except Exception as exc:
            results.append({"currency": source_currency, "status": "fetch_failed", "error": str(exc)})
            continue

        if resp.status_code != 200:
            results.append({
                "currency": source_currency,
                "status": "fetch_failed",
                "http_status": resp.status_code,
            })
            continue

        try:
            data = resp.json()
            rate = float(((data.get("ExchangeRate") or {}).get("Rate")) or 0)
            as_of = ((data.get("ExchangeRate") or {}).get("AsOfDate")) or today
        except Exception as exc:
            results.append({"currency": source_currency, "status": "parse_failed", "error": str(exc)})
            continue

        if rate <= 0:
            results.append({"currency": source_currency, "status": "no_rate"})
            continue

        try:
            db.upsert_fx_rate({
                "organization_id": organization_id,
                "from_currency": source_currency,
                "to_currency": functional_currency,
                "rate": rate,
                "as_of_date": as_of,
                "source": "erp",
                "note": "Synced from QuickBooks /exchangerate",
                "created_by": actor,
            })
            synced += 1
            results.append({
                "currency": source_currency,
                "status": "synced",
                "rate": rate,
                "as_of": as_of,
            })
        except Exception as exc:
            results.append({"currency": source_currency, "status": "upsert_failed", "error": str(exc)})

    return {
        "status": "ok" if synced > 0 else "partial",
        "erp_type": "quickbooks",
        "functional_currency": functional_currency,
        "rates_synced": synced,
        "results": results,
    }


def _qb_access_token(db, organization_id: str) -> Optional[str]:
    """Pull the current QB access token via the standard ERP-credentials path."""
    if not hasattr(db, "get_erp_connection"):
        return None
    try:
        conn = db.get_erp_connection(organization_id, "quickbooks")
    except Exception:
        return None
    if not conn:
        return None
    creds = conn.get("credentials") or {}
    if isinstance(creds, str):
        try:
            import json as _json
            creds = _json.loads(creds)
        except Exception:
            creds = {}
    return (creds or {}).get("access_token")


# ─── Xero ──────────────────────────────────────────────────────────
#
# Xero exposes /api.xro/2.0/CurrencyRates (admin scope) which returns
# rates for the org's base currency vs every defined foreign currency.
# We pull the full set in one call and pick out the ones we need.

def _sync_from_xero(
    db,
    organization_id: str,
    conn: Dict[str, Any],
    functional_currency: str,
    needed: Set[str],
    *,
    actor: str,
) -> Dict[str, Any]:
    if not needed:
        return {"status": "no_currencies_needed", "rates_synced": 0,
                "message": f"No open AP items in non-functional currencies. Functional: {functional_currency}."}
    try:
        import httpx
    except ImportError:
        return {"status": "httpx_unavailable", "rates_synced": 0}

    creds = conn.get("credentials") or {}
    if isinstance(creds, str):
        try:
            import json as _json
            creds = _json.loads(creds)
        except Exception:
            creds = {}
    access_token = (creds or {}).get("access_token")
    tenant_id = str(conn.get("tenant_id") or (creds or {}).get("tenant_id") or "").strip()
    if not access_token or not tenant_id:
        return {"status": "missing_credentials", "rates_synced": 0,
                "message": "Xero credentials incomplete — reconnect."}

    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.xero.com/api.xro/2.0/CurrencyRates",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Xero-Tenant-Id": tenant_id,
                    "Accept": "application/json",
                },
            )
    except Exception as exc:
        return {"status": "fetch_failed", "rates_synced": 0, "error": str(exc)}

    if resp.status_code != 200:
        return {"status": "fetch_failed", "http_status": resp.status_code, "rates_synced": 0}

    try:
        data = resp.json()
        # Xero shape: {"CurrencyRates": [{"FromCurrency":"USD","ToCurrency":"EUR","Rate":0.92}, ...]}
        rate_rows = data.get("CurrencyRates") or []
    except Exception as exc:
        return {"status": "parse_failed", "rates_synced": 0, "error": str(exc)}

    synced = 0
    results: List[Dict[str, Any]] = []
    for source_currency in sorted(needed):
        candidate = next(
            (r for r in rate_rows
             if str(r.get("FromCurrency") or "").upper() == source_currency
             and str(r.get("ToCurrency") or "").upper() == functional_currency),
            None,
        )
        if not candidate:
            results.append({"currency": source_currency, "status": "no_rate"})
            continue
        rate = float(candidate.get("Rate") or 0)
        if rate <= 0:
            results.append({"currency": source_currency, "status": "no_rate"})
            continue
        try:
            db.upsert_fx_rate({
                "organization_id": organization_id,
                "from_currency": source_currency,
                "to_currency": functional_currency,
                "rate": rate,
                "as_of_date": today,
                "source": "erp",
                "note": "Synced from Xero CurrencyRates",
                "created_by": actor,
            })
            synced += 1
            results.append({"currency": source_currency, "status": "synced", "rate": rate, "as_of": today})
        except Exception as exc:
            results.append({"currency": source_currency, "status": "upsert_failed", "error": str(exc)})

    return {
        "status": "ok" if synced > 0 else "partial",
        "erp_type": "xero",
        "functional_currency": functional_currency,
        "rates_synced": synced,
        "results": results,
    }


# ─── NetSuite ──────────────────────────────────────────────────────
#
# NetSuite exposes consolidated exchange rates via the REST record API
# at /services/rest/record/v1/consolidatedExchangeRate. The shape is
# {fromSubsidiary, toSubsidiary, currency, rate, periodFrom...} — for
# Module 9 we just want spot rates against the functional currency,
# so we filter on toSubsidiary's currency = functional and take the
# most-recent record per source currency.

def _sync_from_netsuite(
    db,
    organization_id: str,
    conn: Dict[str, Any],
    functional_currency: str,
    needed: Set[str],
    *,
    actor: str,
) -> Dict[str, Any]:
    if not needed:
        return {"status": "no_currencies_needed", "rates_synced": 0}
    try:
        import httpx
    except ImportError:
        return {"status": "httpx_unavailable", "rates_synced": 0}

    creds = conn.get("credentials") or {}
    if isinstance(creds, str):
        try:
            import json as _json
            creds = _json.loads(creds)
        except Exception:
            creds = {}
    account_id = str((creds or {}).get("account_id") or "").strip()
    access_token = (creds or {}).get("access_token")
    if not account_id or not access_token:
        return {"status": "missing_credentials", "rates_synced": 0,
                "message": "NetSuite credentials incomplete — reconnect."}

    today = datetime.now(timezone.utc).date().isoformat()
    synced = 0
    results: List[Dict[str, Any]] = []

    for source_currency in sorted(needed):
        url = (
            f"https://{account_id}.suitetalk.api.netsuite.com"
            f"/services/rest/record/v1/consolidatedExchangeRate"
            f"?q=fromCurrencyCode IS \"{source_currency}\""
            f" AND toCurrencyCode IS \"{functional_currency}\""
        )
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
        except Exception as exc:
            results.append({"currency": source_currency, "status": "fetch_failed", "error": str(exc)})
            continue

        if resp.status_code != 200:
            results.append({"currency": source_currency, "status": "fetch_failed",
                            "http_status": resp.status_code})
            continue

        try:
            data = resp.json()
            items = data.get("items") or []
            # Latest first; rates table is chronological in NS
            latest = items[0] if items else None
            rate = float(latest.get("currentRate") or 0) if latest else 0
        except Exception as exc:
            results.append({"currency": source_currency, "status": "parse_failed", "error": str(exc)})
            continue

        if rate <= 0:
            results.append({"currency": source_currency, "status": "no_rate"})
            continue

        try:
            db.upsert_fx_rate({
                "organization_id": organization_id,
                "from_currency": source_currency,
                "to_currency": functional_currency,
                "rate": rate,
                "as_of_date": today,
                "source": "erp",
                "note": "Synced from NetSuite consolidatedExchangeRate",
                "created_by": actor,
            })
            synced += 1
            results.append({"currency": source_currency, "status": "synced", "rate": rate, "as_of": today})
        except Exception as exc:
            results.append({"currency": source_currency, "status": "upsert_failed", "error": str(exc)})

    return {
        "status": "ok" if synced > 0 else "partial",
        "erp_type": "netsuite",
        "functional_currency": functional_currency,
        "rates_synced": synced,
        "results": results,
    }


# ─── SAP S/4HANA ───────────────────────────────────────────────────
#
# SAP exposes FX via API_EXCHANGERATESERVICE (OData v4). The shape is
# /A_ExchangeRate?$filter=ExchangeRateType eq 'M' and FromCurrency eq
# 'USD' and ToCurrency eq 'EUR'&$orderby=ValidityStartDate desc&$top=1
# — we ask for the most recent middle-rate per pair. Real-world
# customers also use rate types B (bid) and G (ask); 'M' (mid) is the
# documentation standard and what the FX panel needs.

def _sync_from_sap(
    db,
    organization_id: str,
    conn: Dict[str, Any],
    functional_currency: str,
    needed: Set[str],
    *,
    actor: str,
) -> Dict[str, Any]:
    if not needed:
        return {"status": "no_currencies_needed", "rates_synced": 0}
    try:
        import httpx
    except ImportError:
        return {"status": "httpx_unavailable", "rates_synced": 0}

    base_url = str(conn.get("base_url") or "").strip().rstrip("/")
    creds = conn.get("credentials") or {}
    if isinstance(creds, str):
        try:
            import json as _json
            creds = _json.loads(creds)
        except Exception:
            creds = {}
    access_token = (creds or {}).get("access_token")
    if not base_url or not access_token:
        return {"status": "missing_credentials", "rates_synced": 0,
                "message": "SAP credentials incomplete (need base_url + access_token)."}

    today = datetime.now(timezone.utc).date().isoformat()
    synced = 0
    results: List[Dict[str, Any]] = []

    for source_currency in sorted(needed):
        # OData filter; SAP requires URL-encoded single quotes.
        from urllib.parse import quote
        filt = (
            f"ExchangeRateType eq 'M' "
            f"and FromCurrency eq '{source_currency}' "
            f"and ToCurrency eq '{functional_currency}'"
        )
        url = (
            f"{base_url}/sap/opu/odata4/sap/api_exchangerateservice/srvd_a2x"
            f"/sap/exchangerate/0001/A_ExchangeRate"
            f"?$filter={quote(filt)}&$orderby=ValidityStartDate desc&$top=1"
        )
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
        except Exception as exc:
            results.append({"currency": source_currency, "status": "fetch_failed", "error": str(exc)})
            continue

        if resp.status_code != 200:
            results.append({"currency": source_currency, "status": "fetch_failed",
                            "http_status": resp.status_code})
            continue

        try:
            data = resp.json()
            rows = data.get("value") or []
            row = rows[0] if rows else None
            # SAP rates can be expressed as a ratio (e.g. 1.0/0.92);
            # the REST surface returns the direct rate as `ExchangeRate`.
            rate = float((row or {}).get("ExchangeRate") or 0)
        except Exception as exc:
            results.append({"currency": source_currency, "status": "parse_failed", "error": str(exc)})
            continue

        if rate <= 0:
            results.append({"currency": source_currency, "status": "no_rate"})
            continue

        try:
            db.upsert_fx_rate({
                "organization_id": organization_id,
                "from_currency": source_currency,
                "to_currency": functional_currency,
                "rate": rate,
                "as_of_date": today,
                "source": "erp",
                "note": "Synced from SAP A_ExchangeRate (mid)",
                "created_by": actor,
            })
            synced += 1
            results.append({"currency": source_currency, "status": "synced", "rate": rate, "as_of": today})
        except Exception as exc:
            results.append({"currency": source_currency, "status": "upsert_failed", "error": str(exc)})

    return {
        "status": "ok" if synced > 0 else "partial",
        "erp_type": "sap",
        "functional_currency": functional_currency,
        "rates_synced": synced,
        "results": results,
    }
