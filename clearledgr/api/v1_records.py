"""Public /v1/records router — Box-type-agnostic record read API.

The plan (CUSTOMER_AGENT_CONNECTION_PLAN.md §Step 4) calls for a
generic record API that's Box-type-agnostic from day one, so the
second Box type ships without re-architecting this layer.

Today only ``box_type="ap_item"`` is supported; the second Box type
adds a single ``_RECORD_READERS`` entry and ships.

Endpoints:

* ``GET /v1/records`` — list records.
    Query: ``box_type`` (required), ``state`` (optional),
    ``cursor`` (opaque, optional), ``limit`` (default 50, max 200).
    Scope: ``read:ap_items``.

* ``GET /v1/records/{box_id}`` — single record.
    Query: ``box_type`` (required — kept explicit so the router
    never has to guess from the id shape).
    Scope: ``read:ap_items``.

Response shape (Box-type-agnostic):

    {
        "records": [
            {
                "id":              "<box_id>",
                "box_type":        "ap_item",
                "state":           "needs_approval",
                "organization_id": "org_x",
                "created_at":      "...",
                "updated_at":      "...",
                "data":            {<box-type-specific public fields>}
            },
            ...
        ],
        "next_cursor": "..." | null
    }

Field exposure is deny-by-default. The per-box-type ``_field_set``
allowlist names exactly which row columns surface through the public
API. Bank details, raw error strings, Slack/Teams thread refs, and
metadata blobs never leave the substrate.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from clearledgr.api.v1_auth import AgentIdentity, require_agent_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/records", tags=["v1-records"])


# ─── Box-type registry ─────────────────────────────────────────────


# The fields each box_type publishes through the public API. Deny-by-
# default: every column that ships has to land in this set explicitly.
# Sensitive columns (bank_details_encrypted, slack/teams/gmail refs,
# metadata blob, raw error text) are deliberately absent.
_AP_ITEM_PUBLIC_FIELDS = frozenset({
    "vendor_name",
    "amount",
    "currency",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "approval_required",
    "approved_by",
    "approved_at",
    "rejected_by",
    "rejected_at",
    "rejection_reason",
    "erp_reference",
    "erp_posted_at",
    "approval_surface",
    "approval_policy_version",
    "exception_code",
    "exception_severity",
    "exception_reason",
    "match_status",
    "document_type",
    "entity_id",
    "confidence",
    "owner_id",
    "owner_email",
    "owner_assigned_at",
    "owner_source",
})


class _RecordReader:
    """Per-box-type record-list + record-read shim.

    Each Box type registers one of these so the v1 router stays
    type-agnostic. ``list_fn`` returns ``(rows, total_count)`` for a
    given org/state slice; ``read_fn`` returns one row or ``None``;
    ``fields`` is the field allowlist applied to the rows before
    they leave the API.
    """

    def __init__(
        self,
        *,
        box_type: str,
        fields: frozenset[str],
        list_fn: Callable[..., tuple[List[Dict[str, Any]], Optional[int]]],
        read_fn: Callable[..., Optional[Dict[str, Any]]],
    ) -> None:
        self.box_type = box_type
        self.fields = fields
        self.list_fn = list_fn
        self.read_fn = read_fn


def _list_ap_items(
    db: Any, organization_id: str, *, state: Optional[str],
    offset: int, limit: int,
) -> tuple[List[Dict[str, Any]], Optional[int]]:
    """List AP items for org, optionally filtered by state, with offset+limit.

    Returns ``(rows, total)`` where ``total`` is the count of matching
    rows (used to compute ``next_cursor``).
    """
    where = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    if state:
        where.append("state = %s")
        params.append(state)
    where_clause = " AND ".join(where)

    sql_list = (
        f"SELECT * FROM ap_items WHERE {where_clause} "
        f"ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s"
    )
    sql_count = f"SELECT COUNT(*) AS total FROM ap_items WHERE {where_clause}"

    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql_count, tuple(params))
        total_row = cur.fetchone()
        total = int(dict(total_row).get("total") if total_row else 0)

        cur.execute(sql_list, tuple(params) + (limit, offset))
        rows = [dict(r) for r in (cur.fetchall() or [])]
    return rows, total


def _read_ap_item(
    db: Any, organization_id: str, box_id: str
) -> Optional[Dict[str, Any]]:
    """Read a single AP item, enforcing org match server-side."""
    sql = "SELECT * FROM ap_items WHERE id = %s AND organization_id = %s"
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (box_id, organization_id))
        row = cur.fetchone()
    return dict(row) if row else None


_RECORD_READERS: Dict[str, _RecordReader] = {
    "ap_item": _RecordReader(
        box_type="ap_item",
        fields=_AP_ITEM_PUBLIC_FIELDS,
        list_fn=_list_ap_items,
        read_fn=_read_ap_item,
    ),
}


# ─── Cursor encode/decode ──────────────────────────────────────────


def _encode_cursor(offset: int) -> str:
    """Opaque base64 cursor. Survives transport, hides internal shape."""
    return base64.urlsafe_b64encode(
        json.dumps({"o": offset}).encode("utf-8")
    ).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> int:
    """Parse the offset out of an opaque cursor. Returns 0 for missing
    or malformed cursors (fail-open on pagination — caller restarts
    from the top, never gets a 500)."""
    if not cursor:
        return 0
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        )
        return max(0, int(payload.get("o", 0)))
    except Exception:
        return 0


# ─── Response shaping ──────────────────────────────────────────────


def _shape_record(
    row: Dict[str, Any], *, box_type: str, fields: frozenset[str]
) -> Dict[str, Any]:
    """Apply the public-field allowlist and produce the v1 record shape."""
    data = {k: row.get(k) for k in fields if k in row}
    return {
        "id": row.get("id"),
        "box_type": box_type,
        "state": row.get("state"),
        "organization_id": row.get("organization_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "data": data,
    }


def _error(
    *, status_code: int, error_code: str, message: str,
    request: Optional[Request] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {"error_code": error_code, "message": message}
    rid = getattr(request.state, "correlation_id", None) if request else None
    if rid:
        body["request_id"] = rid
    return JSONResponse(status_code=status_code, content=body)


# ─── Endpoints ─────────────────────────────────────────────────────


class V1RecordResponse(BaseModel):
    id: str
    box_type: str
    state: Optional[str] = None
    organization_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    data: Dict[str, Any]


class V1RecordsListResponse(BaseModel):
    records: List[V1RecordResponse]
    next_cursor: Optional[str] = None


@router.get("", response_model=V1RecordsListResponse)
async def list_records(
    request: Request,
    box_type: str = Query(..., description="Box type to list, e.g. 'ap_item'"),
    state: Optional[str] = Query(
        default=None, description="Filter to records currently in this state"
    ),
    cursor: Optional[str] = Query(
        default=None, description="Opaque pagination cursor"
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Page size"),
    agent: AgentIdentity = Depends(require_agent_key("read:ap_items")),
):
    """List records the caller's organisation owns, filtered by
    ``box_type`` and optionally ``state``."""
    reader = _RECORD_READERS.get(box_type)
    if reader is None:
        return _error(
            status_code=400,
            error_code="unsupported_box_type",
            message=(
                f"box_type={box_type!r} is not exposed through /v1. "
                f"Supported: {sorted(_RECORD_READERS.keys())}"
            ),
            request=request,
        )

    offset = _decode_cursor(cursor)
    from clearledgr.core.database import get_db

    db = get_db()
    try:
        rows, total = reader.list_fn(
            db, agent.organization_id, state=state, offset=offset, limit=limit
        )
    except Exception:
        logger.exception("v1.records list failure (box_type=%s)", box_type)
        return _error(
            status_code=500,
            error_code="internal_error",
            message="internal_error",
            request=request,
        )

    records = [
        _shape_record(row, box_type=box_type, fields=reader.fields)
        for row in rows
    ]
    next_cursor = (
        _encode_cursor(offset + limit)
        if total is not None and (offset + len(records)) < total
        else None
    )
    return {"records": records, "next_cursor": next_cursor}


@router.get("/{box_id}", response_model=V1RecordResponse)
async def read_record(
    box_id: str,
    request: Request,
    box_type: str = Query(..., description="Box type, e.g. 'ap_item'"),
    agent: AgentIdentity = Depends(require_agent_key("read:ap_items")),
):
    """Read a single record by id. ``box_type`` is required so the
    router never has to guess from the id shape (every Box type owns
    its own id namespace, but the API stays explicit)."""
    reader = _RECORD_READERS.get(box_type)
    if reader is None:
        return _error(
            status_code=400,
            error_code="unsupported_box_type",
            message=(
                f"box_type={box_type!r} is not exposed through /v1. "
                f"Supported: {sorted(_RECORD_READERS.keys())}"
            ),
            request=request,
        )

    from clearledgr.core.database import get_db

    db = get_db()
    try:
        row = reader.read_fn(db, agent.organization_id, box_id)
    except Exception:
        logger.exception(
            "v1.records read failure (box_type=%s box_id=%s)", box_type, box_id
        )
        return _error(
            status_code=500,
            error_code="internal_error",
            message="internal_error",
            request=request,
        )

    if row is None:
        return _error(
            status_code=404,
            error_code="not_found",
            message=f"{box_type}:{box_id} not found",
            request=request,
        )

    return _shape_record(row, box_type=box_type, fields=reader.fields)
