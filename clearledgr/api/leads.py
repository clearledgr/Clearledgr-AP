"""Inbound demo-request leads from the marketing site.

The marketing site at clearledgr.com posts form submissions here when
a prospect fills the demo-request form on /contact. Replaces the
Netlify Forms integration after the marketing site moved to Railway.

Public endpoint (no auth). Rate-limited by RateLimitMiddleware. Stores
the submission in the ``marketing_leads`` table for follow-up. The
table is intentionally org-less (organization_id NULL) since these
leads are inbound from anonymous prospects who don't yet have a
tenant.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clearledgr.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Marketing"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_TEXT = 2000
_MAX_SHORT = 200


class LeadIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=_MAX_SHORT)
    name: Optional[str] = Field(None, max_length=_MAX_SHORT)
    company: Optional[str] = Field(None, max_length=_MAX_SHORT)
    role: Optional[str] = Field(None, max_length=_MAX_SHORT)
    volume: Optional[str] = Field(None, max_length=_MAX_SHORT)
    message: Optional[str] = Field(None, max_length=_MAX_TEXT)
    source: Optional[str] = Field(None, max_length=_MAX_SHORT)


def _sanitize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@router.post("/leads", summary="Inbound demo-request lead")
async def submit_lead(payload: LeadIn, request: Request) -> Dict[str, Any]:
    email = _sanitize(payload.email) or ""
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="invalid_email")

    metadata = {
        "user_agent": str(request.headers.get("user-agent") or "")[:_MAX_SHORT],
        "referer": str(request.headers.get("referer") or "")[:_MAX_SHORT],
        "ip": str(request.client.host) if request.client else "",
    }

    lead_id = f"LEAD-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    db.initialize()
    sql = (
        "INSERT INTO marketing_leads "
        "(id, email, name, company, role, volume, message, source, metadata_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (
                lead_id,
                email.lower(),
                _sanitize(payload.name),
                _sanitize(payload.company),
                _sanitize(payload.role),
                _sanitize(payload.volume),
                _sanitize(payload.message),
                _sanitize(payload.source) or "clearledgr.com",
                json.dumps(metadata),
                now,
            ))
            conn.commit()
    except Exception as exc:
        logger.exception("lead persist failed: %s", exc)
        raise HTTPException(status_code=503, detail="lead_persist_failed")

    logger.info(
        "marketing_lead id=%s email=%s company=%s source=%s",
        lead_id, email.lower(),
        _sanitize(payload.company) or "-",
        _sanitize(payload.source) or "clearledgr.com",
    )
    return {"ok": True, "id": lead_id}
