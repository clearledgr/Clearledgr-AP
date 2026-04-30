"""Per-ERP test + credential-rotation operations (Module 5 carry-overs).

The connect flows in workspace_shell.py establish a connection but
embed the test inside the connect POST. ERP admins expect to be
able to re-test an existing connection without re-running the OAuth
or credentials dance, and to rotate API keys / refresh tokens
without re-onboarding.

Two surfaces:

  * ``POST /api/workspace/integrations/erp/{erp_type}/test``
      Runs a read-only call against the configured ERP and reports
      success/failure + latency. Each ERP picks a cheap, idempotent
      endpoint:
        QuickBooks  — GET CompanyInfo
        Xero        — GET Organisations
        NetSuite    — GET /services/rest/record/v1/vendor?limit=1
        SAP B1      — GET /b1s/v1/CompanyService_GetCompanyInfo (or
                       /BusinessPartners?$top=1 as a fallback)
        SAP S/4HANA — GET API_BUSINESS_PARTNER/A_BusinessPartner?$top=1

  * ``POST /api/workspace/integrations/erp/{erp_type}/rotate-credentials``
      Replaces secrets on the existing ERPConnection without losing
      the row's identity. Body shape varies per ERP (QB only refresh
      token; SAP needs username/password or OAuth client secret;
      NetSuite TBA needs the four-piece secret). Audited so a
      compliance review can reconstruct who rotated what when.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/workspace/integrations/erp",
    tags=["erp-connection-ops"],
)


_SUPPORTED_ERPS = ("quickbooks", "xero", "netsuite", "sap")


# ── Test connection ────────────────────────────────────────────────


class TestConnectionResult(BaseModel):
    erp_type: str
    ok: bool
    latency_ms: int
    detail: Optional[str] = None
    response_summary: Optional[Dict[str, Any]] = None


async def _test_quickbooks(connection) -> Dict[str, Any]:
    if not connection.access_token or not connection.realm_id:
        return {"ok": False, "detail": "missing_token_or_realm"}
    from clearledgr.core.http_client import get_http_client
    url = (
        f"https://quickbooks.api.intuit.com/v3/company/"
        f"{connection.realm_id}/companyinfo/{connection.realm_id}"
    )
    client = get_http_client()
    response = await client.get(
        url,
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 401:
        return {"ok": False, "detail": "token_expired", "needs_reauth": True}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"http_{response.status_code}"}
    body = response.json() or {}
    info = (body.get("CompanyInfo") or {})
    return {
        "ok": True,
        "response_summary": {
            "company_name": info.get("CompanyName"),
            "country": info.get("Country"),
        },
    }


async def _test_xero(connection) -> Dict[str, Any]:
    if not connection.access_token or not connection.tenant_id:
        return {"ok": False, "detail": "missing_token_or_tenant"}
    from clearledgr.core.http_client import get_http_client
    url = "https://api.xero.com/api.xro/2.0/Organisation"
    client = get_http_client()
    response = await client.get(
        url,
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "xero-tenant-id": connection.tenant_id,
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 401:
        return {"ok": False, "detail": "token_expired", "needs_reauth": True}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"http_{response.status_code}"}
    body = response.json() or {}
    orgs = body.get("Organisations") or []
    org = orgs[0] if orgs else {}
    return {
        "ok": True,
        "response_summary": {
            "organisation_name": org.get("Name"),
            "country_code": org.get("CountryCode"),
        },
    }


async def _test_netsuite(connection) -> Dict[str, Any]:
    if not connection.access_token or not connection.account_id:
        return {"ok": False, "detail": "missing_token_or_account"}
    from clearledgr.core.http_client import get_http_client
    url = (
        f"https://{connection.account_id}.suitetalk.api.netsuite.com"
        f"/services/rest/record/v1/vendor?limit=1"
    )
    client = get_http_client()
    response = await client.get(
        url,
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 401:
        return {"ok": False, "detail": "token_expired", "needs_reauth": True}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"http_{response.status_code}"}
    body = response.json() or {}
    return {
        "ok": True,
        "response_summary": {
            "vendor_count": body.get("totalResults") or len(body.get("items") or []),
        },
    }


async def _test_sap(connection) -> Dict[str, Any]:
    if not connection.access_token or not connection.base_url:
        return {"ok": False, "detail": "missing_token_or_base_url"}
    from clearledgr.core.http_client import get_http_client
    from clearledgr.integrations.erp_sap import is_sap_s4hana_connection

    base = str(connection.base_url).rstrip("/")
    if is_sap_s4hana_connection(connection):
        # Cheap S/4HANA read: business partner top 1.
        url = (
            f"{base}/sap/opu/odata/sap/API_BUSINESS_PARTNER/"
            f"A_BusinessPartner?$top=1"
        )
        flavor = "s4hana"
    else:
        # B1: top 1 BusinessPartner via Service Layer.
        url = f"{base}/BusinessPartners?$top=1"
        flavor = "b1"
    client = get_http_client()
    response = await client.get(
        url,
        headers={
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 401:
        return {"ok": False, "detail": "token_expired", "needs_reauth": True}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"http_{response.status_code}"}
    body = response.json() or {}
    record = body.get("d") if isinstance(body.get("d"), dict) else body
    return {
        "ok": True,
        "response_summary": {
            "flavor": flavor,
            "results_count": (
                len(record.get("results") or record.get("value") or [])
                if isinstance(record, dict) else 0
            ),
        },
    }


_TEST_DISPATCH = {
    "quickbooks": _test_quickbooks,
    "xero": _test_xero,
    "netsuite": _test_netsuite,
    "sap": _test_sap,
}


@router.post(
    "/{erp_type}/test", response_model=TestConnectionResult,
)
async def test_erp_connection(
    erp_type: str,
    user: TokenData = Depends(get_current_user),
):
    """Run a cheap, idempotent read against the configured ERP and
    report success + latency. Operators use this after rotating
    credentials or troubleshooting a webhook to verify the connection
    is healthy without re-running the connect flow."""
    erp = (erp_type or "").strip().lower()
    if erp not in _SUPPORTED_ERPS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported_erp:{erp!r}; supported={list(_SUPPORTED_ERPS)}",
        )

    from clearledgr.integrations.erp_router import get_erp_connection
    connection = get_erp_connection(user.organization_id, erp)
    if connection is None:
        raise HTTPException(
            status_code=404,
            detail=f"no_{erp}_connection_for_org",
        )

    started = time.monotonic()
    try:
        outcome = await _TEST_DISPATCH[erp](connection)
    except Exception as exc:
        logger.exception(
            "erp test failed unexpectedly: org=%s erp=%s",
            user.organization_id, erp,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return TestConnectionResult(
            erp_type=erp, ok=False, latency_ms=latency_ms,
            detail=f"unexpected_error:{type(exc).__name__}",
        )

    latency_ms = int((time.monotonic() - started) * 1000)

    # Audit emit.
    db = get_db()
    try:
        db.append_audit_event({
            "box_id": f"erp_connection:{erp}:{user.organization_id}",
            "box_type": "erp_connection",
            "event_type": (
                "erp_connection_test_ok" if outcome.get("ok")
                else "erp_connection_test_failed"
            ),
            "actor_type": "user",
            "actor_id": user.user_id,
            "organization_id": user.organization_id,
            "source": "erp_connection_ops",
            "metadata": {
                "erp": erp,
                "latency_ms": latency_ms,
                "detail": outcome.get("detail"),
            },
        })
    except Exception:
        logger.exception("erp test: audit emit failed")

    return TestConnectionResult(
        erp_type=erp,
        ok=bool(outcome.get("ok")),
        latency_ms=latency_ms,
        detail=outcome.get("detail"),
        response_summary=outcome.get("response_summary"),
    )


# ── Credential rotation ───────────────────────────────────────────


class _RotateBody(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    realm_id: Optional[str] = None         # QB
    tenant_id: Optional[str] = None        # Xero
    base_url: Optional[str] = None         # SAP
    company_code: Optional[str] = None     # SAP
    account_id: Optional[str] = None       # NetSuite
    consumer_key: Optional[str] = None     # NetSuite TBA
    consumer_secret: Optional[str] = None
    token_id: Optional[str] = None
    token_secret: Optional[str] = None
    webhook_secret: Optional[str] = None   # any
    subsidiary_id: Optional[str] = None    # NetSuite OneWorld


class RotateResult(BaseModel):
    erp_type: str
    fields_updated: List[str]


@router.post(
    "/{erp_type}/rotate-credentials", response_model=RotateResult,
)
def rotate_erp_credentials(
    erp_type: str,
    body: _RotateBody = Body(...),
    user: TokenData = Depends(get_current_user),
):
    """Replace one or more secrets on the existing ERPConnection.

    Only the fields present in the body are written; absent fields
    keep their prior value. The connection's identity (org_id,
    erp_type) is unchanged so downstream rows (audit chain, AP
    items with erp_reference) stay linked.
    """
    erp = (erp_type or "").strip().lower()
    if erp not in _SUPPORTED_ERPS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported_erp:{erp!r}; supported={list(_SUPPORTED_ERPS)}",
        )

    from clearledgr.integrations.erp_router import (
        get_erp_connection,
        set_erp_connection,
    )
    existing = get_erp_connection(user.organization_id, erp)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"no_{erp}_connection_for_org",
        )

    rotated: List[str] = []
    for field_name in (
        "access_token", "refresh_token", "client_id", "client_secret",
        "realm_id", "tenant_id", "base_url", "company_code",
        "account_id", "consumer_key", "consumer_secret",
        "token_id", "token_secret", "webhook_secret", "subsidiary_id",
    ):
        new_value = getattr(body, field_name, None)
        if new_value is not None:
            setattr(existing, field_name, new_value)
            rotated.append(field_name)

    if not rotated:
        raise HTTPException(
            status_code=400, detail="no_fields_to_rotate",
        )

    set_erp_connection(user.organization_id, existing)

    # Audit. Don't log secret VALUES — only field NAMES rotated.
    db = get_db()
    try:
        db.append_audit_event({
            "box_id": f"erp_connection:{erp}:{user.organization_id}",
            "box_type": "erp_connection",
            "event_type": "erp_credentials_rotated",
            "actor_type": "user",
            "actor_id": user.user_id,
            "organization_id": user.organization_id,
            "source": "erp_connection_ops",
            "metadata": {
                "erp": erp,
                "fields_rotated": rotated,
            },
        })
    except Exception:
        logger.exception("erp rotate: audit emit failed")

    return RotateResult(erp_type=erp, fields_updated=rotated)
