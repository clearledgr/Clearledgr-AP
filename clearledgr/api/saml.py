"""SAML SSO endpoints (Module 6 Pass C).

Two routers:
  * ``saml_admin_router`` — under /api/workspace/saml/, admin-only,
    GET/PUT/DELETE the per-tenant SAML config.
  * ``saml_public_router`` — under /saml/, no auth required (these
    are the IdP-facing flows): SP metadata, login redirect, ACS
    POST. The ACS endpoint validates + JIT-provisions then sets a
    Clearledgr session cookie and redirects to the workspace.

Audit: every login attempt — whether it succeeds, fails validation,
or trips replay protection — emits an ``saml_login_*`` event so a
compliance review can reconstruct who tried to authenticate when.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from clearledgr.core.auth import (
    TokenData,
    create_access_token,
    get_current_user,
)
from clearledgr.core.database import get_db
from clearledgr.services import saml_sso
from clearledgr.services.saml_validator import SAMLValidationError

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────
# Admin router — config CRUD
# ───────────────────────────────────────────────────────────────────

saml_admin_router = APIRouter(prefix="/api/workspace/saml", tags=["saml-admin"])


def _require_admin(user: TokenData) -> None:
    from clearledgr.core.auth import has_admin_access
    if not has_admin_access(user.role):
        raise HTTPException(status_code=403, detail="admin_role_required")


def _resolve_org_id(user: TokenData, requested: Optional[str]) -> str:
    org_id = str(requested or user.organization_id or "default").strip() or "default"
    if org_id != str(user.organization_id or "").strip():
        raise HTTPException(status_code=403, detail="org_access_denied")
    return org_id


def _audit_saml(
    db,
    *,
    organization_id: str,
    event_type: str,
    actor_id: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
) -> None:
    """Best-effort audit emit; never raises."""
    try:
        db.append_audit_event({
            "event_type": event_type,
            "actor_type": "user" if event_type.startswith("saml_config") else "external_idp",
            "actor_id": actor_id,
            "organization_id": organization_id,
            "box_id": organization_id,
            "box_type": "saml_config",
            "source": "saml",
            "payload_json": payload,
            **({"idempotency_key": idempotency_key} if idempotency_key else {}),
        })
    except Exception as exc:
        logger.warning(
            "[saml] audit emit %s failed for org=%s: %s",
            event_type, organization_id, exc,
        )


class SAMLConfigPutRequest(BaseModel):
    enabled: bool = True
    idp_entity_id: str = Field(..., min_length=1, max_length=512)
    idp_sso_url: str = Field(..., min_length=1, max_length=1024)
    idp_certificate_pem: str = Field(..., min_length=64, max_length=16384)
    sp_entity_id: str = Field(..., min_length=1, max_length=512)
    sp_acs_url: str = Field(..., min_length=1, max_length=1024)
    attribute_email: str = Field(default="email", max_length=128)
    attribute_role: Optional[str] = Field(default=None, max_length=128)
    attribute_entity: Optional[str] = Field(default=None, max_length=128)
    default_role: str = Field(default="ap_clerk", max_length=64)
    default_entity_id: Optional[str] = Field(default=None, max_length=128)
    jit_provisioning: bool = True
    # Optional Single Logout endpoints. When set, SP-initiated
    # logout redirects users to idp_slo_url so the IdP closes its
    # session in addition to ours. sp_slo_url is what the IdP uses
    # to send a LogoutRequest to us (echoed in SP metadata).
    idp_slo_url: Optional[str] = Field(default=None, max_length=1024)
    sp_slo_url: Optional[str] = Field(default=None, max_length=1024)


@saml_admin_router.get("/config")
def get_saml_config_endpoint(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Return the per-tenant SAML config with the cert redacted to a
    fingerprint.

    ``200`` with ``configured=false`` when no config exists — keeps the
    UI's GET probe simple.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    cfg = saml_sso.get_saml_config(db, org_id)
    if not cfg:
        return {
            "organization_id": org_id,
            "configured": False,
            "config": None,
        }
    return {
        "organization_id": org_id,
        "configured": True,
        "config": cfg.to_redacted_dict(),
    }


@saml_admin_router.put("/config")
def put_saml_config_endpoint(
    body: SAMLConfigPutRequest,
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Create or replace the per-tenant SAML config.

    Validates IdP cert PEM (must parse as X.509), SSO URL is https,
    SP entity id is non-empty, etc. Audit-emitted with the IdP entity
    id and cert fingerprint so the change is reconstructable.
    """
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()

    config = saml_sso.SAMLConfig(
        enabled=body.enabled,
        idp_entity_id=body.idp_entity_id.strip(),
        idp_sso_url=body.idp_sso_url.strip(),
        idp_certificate_pem=body.idp_certificate_pem.strip(),
        sp_entity_id=body.sp_entity_id.strip(),
        sp_acs_url=body.sp_acs_url.strip(),
        attribute_email=body.attribute_email.strip() or "email",
        attribute_role=(body.attribute_role or "").strip() or None,
        attribute_entity=(body.attribute_entity or "").strip() or None,
        default_role=(body.default_role or "ap_clerk").strip().lower(),
        default_entity_id=(body.default_entity_id or "").strip() or None,
        jit_provisioning=body.jit_provisioning,
        idp_slo_url=(body.idp_slo_url or "").strip() or None,
        sp_slo_url=(body.sp_slo_url or "").strip() or None,
    )
    try:
        saml_sso.save_saml_config(db, org_id, config)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"reason": "validation_failed", "message": str(exc)},
        )
    redacted = config.to_redacted_dict()
    _audit_saml(
        db,
        organization_id=org_id,
        event_type="saml_config_updated",
        actor_id=str(getattr(user, "user_id", "") or "unknown"),
        payload={
            "actor_email": getattr(user, "email", None),
            "idp_entity_id": config.idp_entity_id,
            "idp_sso_url": config.idp_sso_url,
            "sp_entity_id": config.sp_entity_id,
            "enabled": config.enabled,
            "jit_provisioning": config.jit_provisioning,
            "cert_fingerprint": (redacted.get("idp_certificate") or {}).get("fingerprint_sha256"),
        },
    )
    return {"organization_id": org_id, "configured": True, "config": redacted}


@saml_admin_router.delete("/config")
def delete_saml_config_endpoint(
    organization_id: Optional[str] = Query(default=None),
    user: TokenData = Depends(get_current_user),
):
    """Remove the SAML config. Audit-emitted."""
    _require_admin(user)
    org_id = _resolve_org_id(user, organization_id)
    db = get_db()
    deleted = saml_sso.delete_saml_config(db, org_id)
    if deleted:
        _audit_saml(
            db,
            organization_id=org_id,
            event_type="saml_config_deleted",
            actor_id=str(getattr(user, "user_id", "") or "unknown"),
            payload={"actor_email": getattr(user, "email", None)},
        )
    return {"organization_id": org_id, "deleted": deleted}


# ───────────────────────────────────────────────────────────────────
# Public router — IdP-facing flows
# ───────────────────────────────────────────────────────────────────

saml_public_router = APIRouter(prefix="/saml", tags=["saml-public"])


@saml_public_router.get("/{organization_id}/sp-metadata")
def sp_metadata(organization_id: str):
    """Return the SP metadata XML for one tenant.

    Public endpoint — IdPs need to fetch this to register Clearledgr
    as a SAML SP. The metadata is derived from the persisted SAML
    config; if the tenant has no config we 404 rather than emit a
    half-formed metadata blob.
    """
    db = get_db()
    cfg = saml_sso.get_saml_config(db, organization_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="saml_not_configured")
    xml = saml_sso.render_sp_metadata(
        sp_entity_id=cfg.sp_entity_id, sp_acs_url=cfg.sp_acs_url,
    )
    return Response(content=xml, media_type="application/samlmetadata+xml")


@saml_public_router.get("/{organization_id}/login")
def saml_login_initiate(
    organization_id: str,
    relay: Optional[str] = Query(default=None, max_length=512, alias="RelayState"),
):
    """Begin SP-initiated SSO. Builds AuthnRequest + redirects to IdP.

    The optional ``RelayState`` parameter is opaque from the IdP's
    perspective and is echoed back in the ACS POST so we can land
    the user on the page they originally came from. We constrain it
    to <512 chars to prevent an open-redirect-style abuse.
    """
    db = get_db()
    cfg = saml_sso.get_saml_config(db, organization_id)
    if not cfg or not cfg.enabled:
        raise HTTPException(status_code=404, detail="saml_not_configured")

    redirect_url, request_id = saml_sso.build_authn_request_redirect(
        config=cfg, relay_state=relay,
    )
    _audit_saml(
        db,
        organization_id=organization_id,
        event_type="saml_login_initiated",
        actor_id=request_id,
        payload={"idp_entity_id": cfg.idp_entity_id, "request_id": request_id},
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@saml_public_router.post("/{organization_id}/acs")
async def saml_acs(
    organization_id: str,
    request: Request,
    SAMLResponse: str = Form(...),
    RelayState: Optional[str] = Form(default=None),
):
    """Assertion Consumer Service.

    Validates the SAMLResponse (signature, audience, expiry, replay)
    via ``saml_sso.handle_assertion``, JIT-provisions the user if
    enabled, then issues a Clearledgr session JWT and redirects to
    the workspace shell.

    Every outcome — success or failure — emits an audit event.
    Failure modes are surfaced as a structured 401/403 with a token
    reason (signature_invalid, expired, audience_mismatch, ...) so
    the operator can debug from the audit log without seeing the
    raw assertion.
    """
    db = get_db()
    try:
        outcome = saml_sso.handle_assertion(
            db=db,
            organization_id=organization_id,
            response_b64=SAMLResponse,
        )
    except SAMLValidationError as exc:
        reason = str(exc) or "validation_failed"
        _audit_saml(
            db,
            organization_id=organization_id,
            event_type="saml_login_failed",
            actor_id="unknown",
            payload={"reason": reason},
        )
        if reason == "replay_detected":
            raise HTTPException(status_code=409, detail={"reason": reason})
        raise HTTPException(status_code=401, detail={"reason": reason})

    _audit_saml(
        db,
        organization_id=organization_id,
        event_type="saml_login_success",
        actor_id=outcome.user_id,
        payload={
            "user_email": outcome.user_email,
            "user_role": outcome.user_role,
            "is_new_user": outcome.is_new_user,
            "assertion_id": outcome.assertion_id,
        },
    )

    # Issue a Clearledgr session JWT and redirect to the workspace
    # shell. The relay_state, if provided and looks like a workspace
    # path, becomes the redirect target — capped to in-app paths to
    # block open-redirect abuse.
    token = create_access_token(
        user_id=outcome.user_id,
        email=outcome.user_email,
        organization_id=organization_id,
        role=outcome.user_role,
    )
    target_path = "/workspace"
    if RelayState and RelayState.startswith("/workspace"):
        # Strip any embedded ?or#whatever; we only allow the path slot.
        # That stops an attacker from feeding a JS bookmarklet through
        # RelayState.
        target_path = RelayState.split("?")[0].split("#")[0]
    response = RedirectResponse(url=target_path, status_code=302)
    response.set_cookie(
        key="clearledgr_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 8,
        path="/",
    )
    return response


# ── Single Logout (SLO) ────────────────────────────────────────────


@saml_public_router.get("/{organization_id}/logout")
def saml_logout_initiate(
    organization_id: str,
    relay: Optional[str] = Query(default=None, max_length=512, alias="RelayState"),
):
    """SP-initiated Single Logout.

    Clears the local Clearledgr session cookie and (if the IdP
    published an SLO endpoint in the SAML config) redirects to the
    IdP's SLO URL so the IdP also closes its session. This is the
    'sign out' button's target.

    No XML signing in v1 — most IdPs accept an unsigned LogoutRequest
    over the redirect binding when the SP entity id matches a known
    federation. Cert-pinned signed LogoutRequest is the design-doc
    deferred hardening.
    """
    db = get_db()
    cfg = saml_sso.get_saml_config(db, organization_id)
    if not cfg:
        # No SAML configured — just clear the cookie and bounce to
        # workspace login. Operators of non-SAML tenants shouldn't be
        # able to hit a 404 from a "sign out" button.
        response = RedirectResponse(url="/workspace/login", status_code=302)
        response.delete_cookie("clearledgr_session", path="/")
        return response

    _audit_saml(
        db,
        organization_id=organization_id,
        event_type="saml_logout_initiated",
        actor_id="sp_initiated",
        payload={
            "idp_slo_url": cfg.idp_slo_url,
            "relay": relay,
        },
    )

    if cfg.idp_slo_url:
        response = RedirectResponse(url=cfg.idp_slo_url, status_code=302)
    else:
        # No IdP SLO endpoint configured — local-only logout.
        response = RedirectResponse(url="/workspace/login", status_code=302)
    response.delete_cookie("clearledgr_session", path="/")
    return response


@saml_public_router.post("/{organization_id}/slo")
async def saml_slo_callback(
    organization_id: str,
    request: Request,
):
    """SLO callback for IdP-initiated logout.

    The IdP POSTs either a ``SAMLRequest`` (asking us to log the user
    out) or a ``SAMLResponse`` (confirming our SP-initiated logout
    completed). Either way we clear the session cookie + audit-emit.

    Full XML signature validation on the LogoutRequest is the
    design-doc deferred hardening (same family as the SLO redirect-
    binding signing). For now we treat the inbound POST as
    advisory: clear the session locally and respond 200 so the IdP
    doesn't retry.
    """
    db = get_db()

    form_data = {}
    try:
        form = await request.form()
        form_data = dict(form)
    except Exception:
        pass

    direction = (
        "logout_request" if form_data.get("SAMLRequest")
        else "logout_response" if form_data.get("SAMLResponse")
        else "unknown"
    )

    _audit_saml(
        db,
        organization_id=organization_id,
        event_type="saml_logout_callback_received",
        actor_id="idp_initiated",
        payload={
            "direction": direction,
            "relay_state": form_data.get("RelayState"),
        },
    )

    response = Response(status_code=200, content="")
    response.delete_cookie("clearledgr_session", path="/")
    return response
