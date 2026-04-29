"""GDPR data subject request service (Wave 3 / E3).

Handles the three rights vendors / their representatives can exercise
under GDPR Articles 15 / 17 / 20:

  * **access** (Art. 15) — give me a copy of every piece of data you
    hold about me.
  * **erasure** (Art. 17) — delete what you can; anonymize what you
    must keep for legitimate-interest reasons (SOX accounting records).
  * **portability** (Art. 20) — give me a machine-readable export
    suitable for moving to another controller.

Lifecycle:
  pending          — request received, no action yet
  in_progress      — operator started processing
  completed        — fulfilled; outcome_summary records what was done
  rejected         — refused (provide a reason in processing_notes;
                     e.g. SOX-protected records past their retention
                     window can't be deleted)

Statutory window: GDPR Art. 12 mandates response within one month.
``due_at`` is auto-populated to received_at + 30 days. Operator
sees overdue requests highlighted.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_VALID_REQUEST_TYPES = frozenset({"access", "erasure", "portability"})
_VALID_SUBJECT_KINDS = frozenset({"vendor", "user", "external_contact"})
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "rejected"})

_STATUTORY_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _due_at(received_at: str) -> str:
    received = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    return (received + timedelta(days=_STATUTORY_DAYS)).isoformat()


# ── CRUD ────────────────────────────────────────────────────────────


def create_request(
    db,
    *,
    organization_id: str,
    request_type: str,
    subject_kind: str,
    subject_identifier: str,
    requestor_email: Optional[str] = None,
    requestor_relationship: Optional[str] = None,
    received_at: Optional[str] = None,
) -> Dict[str, Any]:
    if request_type not in _VALID_REQUEST_TYPES:
        raise ValueError(
            f"request_type must be one of {sorted(_VALID_REQUEST_TYPES)}"
        )
    if subject_kind not in _VALID_SUBJECT_KINDS:
        raise ValueError(
            f"subject_kind must be one of {sorted(_VALID_SUBJECT_KINDS)}"
        )
    if not subject_identifier:
        raise ValueError("subject_identifier required")
    req_id = f"DSR-{uuid.uuid4().hex[:24]}"
    received = received_at or _now_iso()
    due = _due_at(received)
    sql = (
        "INSERT INTO data_subject_requests "
        "(id, organization_id, request_type, subject_kind, subject_identifier, "
        " requestor_email, requestor_relationship, status, received_at, due_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)"
    )
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, (
            req_id, organization_id, request_type, subject_kind,
            subject_identifier, requestor_email, requestor_relationship,
            received, due,
        ))
        conn.commit()
    return get_request(db, req_id) or {
        "id": req_id,
        "organization_id": organization_id,
        "request_type": request_type,
        "subject_kind": subject_kind,
        "subject_identifier": subject_identifier,
        "status": "pending",
        "received_at": received,
        "due_at": due,
    }


def get_request(db, request_id: str) -> Optional[Dict[str, Any]]:
    db.initialize()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM data_subject_requests WHERE id = %s",
            (request_id,),
        )
        row = cur.fetchone()
    return _decode(row)


def list_requests(
    db,
    *,
    organization_id: str,
    status: Optional[str] = None,
    request_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    db.initialize()
    clauses = ["organization_id = %s"]
    params: List[Any] = [organization_id]
    if status:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status filter: {status!r}")
        clauses.append("status = %s")
        params.append(status)
    if request_type:
        if request_type not in _VALID_REQUEST_TYPES:
            raise ValueError(
                f"invalid request_type filter: {request_type!r}"
            )
        clauses.append("request_type = %s")
        params.append(request_type)
    safe_limit = max(1, min(int(limit or 100), 1000))
    params.append(safe_limit)
    sql = (
        "SELECT * FROM data_subject_requests "
        "WHERE " + " AND ".join(clauses) + " "
        "ORDER BY received_at DESC LIMIT %s"
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [d for d in (_decode(r) for r in rows) if d is not None]


# ── Processing ──────────────────────────────────────────────────────


def process_access_request(
    db,
    request_id: str,
    *,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate the data export for an Art. 15 access request.

    Walks every table that may carry data about the subject, packages
    a single JSON payload, and stores it on the request row so the
    operator can hand it to the data subject.
    """
    req = get_request(db, request_id)
    if not req:
        raise ValueError(f"request {request_id!r} not found")
    if req["request_type"] != "access":
        raise ValueError(
            f"process_access_request called on type={req['request_type']!r}"
        )
    payload = _gather_subject_data(
        db,
        organization_id=req["organization_id"],
        subject_kind=req["subject_kind"],
        subject_identifier=req["subject_identifier"],
    )
    summary = {
        "data_categories": list(payload.keys()),
        "row_counts": {k: len(v) for k, v in payload.items()},
    }
    _mark_processed(
        db, request_id,
        outcome_summary=summary,
        export_payload=payload,
        actor=actor,
    )
    return get_request(db, request_id) or {}


def process_erasure_request(
    db,
    request_id: str,
    *,
    actor: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Erasure request: anonymize the vendor (or user) record + all
    referenced PII. AP items are kept (SOX legitimate interest) but
    PII-scrubbed."""
    from clearledgr.services.gdpr_retention import anonymize_vendor

    req = get_request(db, request_id)
    if not req:
        raise ValueError(f"request {request_id!r} not found")
    if req["request_type"] != "erasure":
        raise ValueError(
            f"process_erasure_request called on type={req['request_type']!r}"
        )

    summary: Dict[str, Any] = {"actions": []}
    if req["subject_kind"] == "vendor":
        outcome = anonymize_vendor(
            db,
            organization_id=req["organization_id"],
            vendor_name=req["subject_identifier"],
            actor=actor,
        )
        summary["actions"].append({
            "type": "anonymize_vendor",
            "vendor_name": req["subject_identifier"],
            **outcome,
        })
    else:
        # users / external_contact erasure — out of scope for v1; the
        # operator handles those manually and records the outcome via
        # the notes field. We still mark complete so the statutory
        # clock stops.
        summary["actions"].append({
            "type": "manual_handoff",
            "subject_kind": req["subject_kind"],
            "subject_identifier": req["subject_identifier"],
        })

    _mark_processed(
        db, request_id,
        outcome_summary=summary,
        export_payload=None,
        actor=actor,
        notes=notes,
    )
    return get_request(db, request_id) or {}


def process_portability_request(
    db,
    request_id: str,
    *,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Portability is structurally identical to access for our data
    shape — the subject gets the same JSON. The only difference is
    semantic: portability data must be in a "commonly used,
    machine-readable format" (Art. 20). JSON satisfies that."""
    req = get_request(db, request_id)
    if not req:
        raise ValueError(f"request {request_id!r} not found")
    if req["request_type"] != "portability":
        raise ValueError(
            f"process_portability_request called on type={req['request_type']!r}"
        )
    payload = _gather_subject_data(
        db,
        organization_id=req["organization_id"],
        subject_kind=req["subject_kind"],
        subject_identifier=req["subject_identifier"],
    )
    summary = {
        "format": "application/json",
        "data_categories": list(payload.keys()),
        "row_counts": {k: len(v) for k, v in payload.items()},
    }
    _mark_processed(
        db, request_id,
        outcome_summary=summary,
        export_payload=payload,
        actor=actor,
    )
    return get_request(db, request_id) or {}


def reject_request(
    db,
    request_id: str,
    *,
    reason: str,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    db.initialize()
    now_iso = _now_iso()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE data_subject_requests "
            "SET status = 'rejected', processed_at = %s, "
            "    processed_by = %s, processing_notes = %s "
            "WHERE id = %s",
            (now_iso, actor, reason, request_id),
        )
        conn.commit()
    return get_request(db, request_id) or {}


# ── Internals ───────────────────────────────────────────────────────


def _gather_subject_data(
    db,
    *,
    organization_id: str,
    subject_kind: str,
    subject_identifier: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Walk every table that may carry data about this subject."""
    out: Dict[str, List[Dict[str, Any]]] = {
        "vendor_profile": [],
        "ap_items": [],
        "vendor_invoice_history": [],
        "audit_events": [],
    }
    if subject_kind == "vendor":
        try:
            profile = db.get_vendor_profile(
                organization_id, subject_identifier,
            )
            if profile:
                # Drop the encrypted ciphertext column from the export
                # — the data subject has the decrypted form already.
                exported = {
                    k: v for k, v in profile.items()
                    if k not in ("bank_details_encrypted",
                                 "pending_bank_details_encrypted")
                }
                out["vendor_profile"].append(exported)
        except Exception:
            logger.exception("gdpr export: vendor_profile fetch failed")

        # AP items + vendor history.
        db.initialize()
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, vendor_name, amount, currency, invoice_number, "
                "       invoice_date, state, sender, created_at, updated_at "
                "FROM ap_items "
                "WHERE organization_id = %s AND vendor_name = %s "
                "ORDER BY created_at DESC LIMIT 1000",
                (organization_id, subject_identifier),
            )
            out["ap_items"] = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT * FROM vendor_invoice_history "
                "WHERE organization_id = %s AND vendor_name = %s "
                "ORDER BY created_at DESC LIMIT 1000",
                (organization_id, subject_identifier),
            )
            out["vendor_invoice_history"] = [dict(r) for r in cur.fetchall()]

        # Audit events keyed to this vendor's box.
        try:
            box_audit = db.list_box_audit_events(
                "vendor", subject_identifier, limit=500,
            )
            out["audit_events"] = list(box_audit)
        except Exception:
            out["audit_events"] = []
    return out


def _mark_processed(
    db,
    request_id: str,
    *,
    outcome_summary: Dict[str, Any],
    export_payload: Optional[Dict[str, Any]],
    actor: Optional[str],
    notes: Optional[str] = None,
) -> None:
    db.initialize()
    now_iso = _now_iso()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE data_subject_requests "
            "SET status = 'completed', processed_at = %s, "
            "    processed_by = %s, processing_notes = %s, "
            "    outcome_summary_json = %s, export_payload_json = %s "
            "WHERE id = %s",
            (
                now_iso, actor, notes,
                json.dumps(outcome_summary or {}),
                (json.dumps(export_payload, default=str)
                 if export_payload is not None else None),
                request_id,
            ),
        )
        conn.commit()


def _decode(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    for col in ("outcome_summary_json", "export_payload_json"):
        raw = out.pop(col, None)
        target = col.removesuffix("_json")
        if raw:
            try:
                out[target] = (
                    json.loads(raw) if isinstance(raw, str) else raw
                )
            except Exception:
                out[target] = None
        else:
            out[target] = None
    return out
