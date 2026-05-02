"""ERP test-transaction probe — Module 5 spec line 183.

"Test action: run a test transaction that verifies the agent can read
and write to the ERP." Each ERP exposes a different "ping" surface;
this service unifies them so the connection-health panel and the
onboarding wizard can both ask "is this connection actually live?"
and get the same answer.

Probe shape per ERP:
  - QuickBooks: GET /v3/company/{realm}/companyinfo/{realm}
  - Xero: GET /api.xro/2.0/Organisations
  - NetSuite: GET /services/rest/record/v1/subsidiary?limit=1
  - SAP: GET /sap/opu/odata/sap/API_BUSINESS_USER_SRV/A_BusinessUser?$top=1

Each returns ``status, latency_ms, http_status, error?`` so callers
can render a single result envelope. Latency is wall-clock from
request send → response received, including SSL/TCP handshake — a
realistic upper bound on what an operator would experience.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def probe_erp_connection(db, organization_id: str, erp_type: Optional[str] = None) -> Dict[str, Any]:
    """Run the ERP-specific test-transaction probe.

    If `erp_type` is None, probes the org's primary connection.
    Returns:
      {
        "status": "ok" | "failed" | "not_supported" | "no_connection",
        "erp_type": "...",
        "latency_ms": int | None,
        "http_status": int | None,
        "error": "..." | None,
        "probed_at": iso8601,
      }
    """
    from datetime import datetime, timezone

    conns = []
    if hasattr(db, "get_erp_connections"):
        try:
            conns = db.get_erp_connections(organization_id) or []
        except Exception:
            conns = []
    if erp_type:
        conn = next(
            (c for c in conns if str(c.get("erp_type") or "").lower() == erp_type.lower()),
            None,
        )
    else:
        conn = next((c for c in conns if c.get("is_active", 1)), None) or (conns[0] if conns else None)

    probed_at = datetime.now(timezone.utc).isoformat()
    if not conn:
        return {
            "status": "no_connection",
            "erp_type": erp_type,
            "latency_ms": None,
            "http_status": None,
            "error": "No ERP connection found.",
            "probed_at": probed_at,
        }

    actual_erp = str(conn.get("erp_type") or "").strip().lower()
    if actual_erp == "quickbooks":
        result = _probe_quickbooks(db, organization_id, conn)
    elif actual_erp == "xero":
        result = _probe_xero(db, organization_id, conn)
    elif actual_erp == "netsuite":
        result = _probe_netsuite(conn)
    elif actual_erp == "sap":
        result = _probe_sap(conn)
    else:
        result = {
            "status": "not_supported",
            "error": f"No probe implemented for ERP type '{actual_erp}'.",
            "latency_ms": None,
            "http_status": None,
        }

    result["erp_type"] = actual_erp
    result["probed_at"] = probed_at

    # Stamp the latest probe latency on the integration row so the
    # connection_health panel can surface it without re-probing.
    if hasattr(db, "set_erp_probe_result"):
        try:
            db.set_erp_probe_result(
                organization_id=organization_id,
                erp_type=actual_erp,
                latency_ms=result.get("latency_ms"),
                status=result.get("status"),
                probed_at=probed_at,
            )
        except Exception as exc:
            logger.debug("[erp_test_probe] persist failed: %s", exc)
    return result


def _qb_access_token(db, organization_id: str) -> Optional[str]:
    if not hasattr(db, "get_erp_connection"):
        return None
    try:
        conn = db.get_erp_connection(organization_id, "quickbooks")
    except Exception:
        return None
    creds = (conn or {}).get("credentials") or {}
    if isinstance(creds, str):
        try:
            import json as _json
            creds = _json.loads(creds)
        except Exception:
            creds = {}
    return (creds or {}).get("access_token")


def _probe_quickbooks(db, organization_id: str, conn: Dict[str, Any]) -> Dict[str, Any]:
    realm_id = str(conn.get("realm_id") or "").strip()
    if not realm_id:
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "QuickBooks connection has no realm_id."}
    access_token = _qb_access_token(db, organization_id)
    if not access_token:
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "QuickBooks access token unavailable."}
    base_url = str(conn.get("base_url") or "https://quickbooks.api.intuit.com").rstrip("/")
    url = f"{base_url}/v3/company/{realm_id}/companyinfo/{realm_id}"
    return _http_probe(url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })


def _probe_xero(db, organization_id: str, conn: Dict[str, Any]) -> Dict[str, Any]:
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
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "Xero credentials incomplete (need access_token + tenant_id)."}
    return _http_probe(
        "https://api.xero.com/api.xro/2.0/Organisations",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Xero-Tenant-Id": tenant_id,
            "Accept": "application/json",
        },
    )


def _probe_netsuite(conn: Dict[str, Any]) -> Dict[str, Any]:
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
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "NetSuite credentials incomplete."}
    return _http_probe(
        f"https://{account_id}.suitetalk.api.netsuite.com/services/rest/record/v1/subsidiary?limit=1",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )


def _probe_sap(conn: Dict[str, Any]) -> Dict[str, Any]:
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
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "SAP credentials incomplete (need base_url + access_token)."}
    return _http_probe(
        f"{base_url}/sap/opu/odata4/sap/api_business_user/srvd_a2x/sap/businessuser/0001/BusinessUser?$top=1",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )


def _http_probe(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """Fire one GET, time the wall-clock, return the result envelope."""
    try:
        import httpx
    except ImportError:
        return {"status": "failed", "latency_ms": None, "http_status": None,
                "error": "httpx not installed."}
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if resp.status_code == 200:
            return {"status": "ok", "latency_ms": latency_ms, "http_status": 200, "error": None}
        return {
            "status": "failed",
            "latency_ms": latency_ms,
            "http_status": resp.status_code,
            "error": f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"status": "failed", "latency_ms": latency_ms, "http_status": None,
                "error": str(exc)}
