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
    if erp_type in {"xero", "netsuite", "sap"}:
        return {
            "status": "not_supported",
            "erp_type": erp_type,
            "message": (
                f"FX sync from {erp_type.title()} is not yet implemented. "
                "Use manual rate entry for now; the connector lands in a follow-up."
            ),
            "rates_synced": 0,
            "currencies": sorted(needed),
        }
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
