"""FastAPI dependencies for Clearledgr core services."""
import logging
from typing import Optional

from fastapi import HTTPException, Request

from clearledgr.di.container import container

logger = logging.getLogger(__name__)


def get_audit_service():
    return container.audit()


def get_llm_service():
    return container.llm()


def get_exception_router():
    return container.exceptions()


# Note: get_learning_service was removed. LearningService holds an
# organization_id on self, so a process-wide singleton would pin to
# the first caller's org and silently leak data into other tenants'
# queries. There were zero callers of this dep, but having it on the
# DI container was a tripwire — the next route to `Depends(...)` it
# would have shipped a cross-tenant leak. Use the per-org factory
# `clearledgr.services.learning.get_learning_service(organization_id)`
# directly inside the handler instead.


def get_sap_adapter():
    return container.sap()


async def soft_org_guard(request: Request, user: Optional[object] = None) -> None:
    """Router-level dependency: enforce org isolation for JWT-authenticated callers.

    Reads ``organization_id`` from query params (covers all GET and query-param
    POST routes).  Body-param routes are protected by the per-item org check
    already present in each handler.

    Unauthenticated callers (Slack webhooks, ERP callbacks, autopilot) pass
    through unchanged — backwards-compatible.
    """

    # Resolve the optional user from the request directly so this works as a
    # plain function dependency (FastAPI will inject Request automatically).
    try:
        auth_header = request.headers.get("Authorization", "")
        api_key = request.headers.get("X-API-Key")
        resolved_user = None
        if auth_header.startswith("Bearer ") or api_key:
            from clearledgr.core.auth import decode_token, _token_data_from_payload
            from clearledgr.core.database import get_db
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                try:
                    payload = decode_token(token)
                    resolved_user = _token_data_from_payload(payload)
                except Exception as exc:
                    logger.warning("Bearer token decode failed: %s", exc)
            if resolved_user is None and api_key:
                db = get_db()
                key_record = db.validate_api_key(api_key)
                if key_record:
                    from clearledgr.core.auth import TokenData
                    from datetime import datetime, timezone, timedelta
                    resolved_user = TokenData(
                        user_id=key_record.get("user_id", "api_user"),
                        email="api@system",
                        organization_id=key_record["organization_id"],
                        role="api",
                        exp=datetime.now(timezone.utc) + timedelta(hours=1),
                    )
    except Exception:
        resolved_user = None

    if resolved_user is None:
        return  # unauthenticated — pass through

    org_id = request.query_params.get("organization_id")
    if org_id:
        token_org = getattr(resolved_user, "organization_id", None)
        if token_org and str(token_org) != str(org_id):
            raise HTTPException(
                status_code=403,
                detail="org_mismatch: token organization does not match requested organization",
            )


def verify_org_access(claimed_org_id: str, user: Optional[object]) -> None:
    """Raise 403 if an authenticated user's org_id does not match claimed_org_id.

    When ``user`` is None (unauthenticated integration call — Slack webhook,
    ERP callback, autopilot) the check is skipped to preserve backwards
    compatibility.  Protection applies only when a valid JWT/API-key is
    present.
    """
    if user is None:
        return
    token_org = getattr(user, "organization_id", None)
    if token_org and str(token_org) != str(claimed_org_id):
        raise HTTPException(
            status_code=403,
            detail="org_mismatch: token organization does not match requested organization",
        )
