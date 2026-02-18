"""AP item APIs used by the Gmail extension focus-first sidebar."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from clearledgr.core.database import ClearledgrDB, get_db
from clearledgr.services.ap_context_connectors import build_multi_system_context


router = APIRouter(prefix="/api/ap/items", tags=["ap-items"])


class LinkSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)
    subject: Optional[str] = None
    sender: Optional[str] = None
    detected_at: Optional[str] = None
    metadata: Dict[str, Any] = {}


class MergeItemsRequest(BaseModel):
    source_ap_item_id: str = Field(..., min_length=1)
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_merge", min_length=1)


class SplitSourceRequest(BaseModel):
    source_type: str = Field(..., min_length=1)
    source_ref: str = Field(..., min_length=1)


class SplitItemRequest(BaseModel):
    actor_id: str = Field(default="system", min_length=1)
    reason: str = Field(default="manual_split", min_length=1)
    sources: List[SplitSourceRequest] = []


def _parse_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_iso(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_budget_checks(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        for key in ("checks", "budgets", "budget_impact"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [entry for entry in nested if isinstance(entry, dict)]
        if raw.get("budget_name") or raw.get("after_approval_status"):
            return [raw]
    return []


def _budget_status_rank(status: str) -> int:
    normalized = str(status or "").strip().lower()
    if normalized == "exceeded":
        return 4
    if normalized == "critical":
        return 3
    if normalized == "warning":
        return 2
    if normalized == "healthy":
        return 1
    return 0


def _normalize_exception_code(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"po_required_missing", "po_missing_reference"}:
        return "po_missing_reference"
    if raw.startswith("po_match_") or raw in {"po_amount_mismatch", "po_quantity_mismatch"}:
        return "po_amount_mismatch"
    if raw in {"budget_exceeded", "budget_critical", "budget_overrun"}:
        return "budget_overrun"
    if raw.startswith("policy_") or raw == "policy_validation_failed":
        return "policy_validation_failed"
    if raw == "missing_budget_context":
        return raw
    return raw


def _normalize_exception_severity(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw in {"critical", "error"}:
        return "critical"
    if raw in {"high", "major"}:
        return "high"
    if raw in {"medium", "warning"}:
        return "medium"
    if raw in {"low", "info"}:
        return "low"
    return None


def _default_severity_for_exception(code: Optional[str]) -> Optional[str]:
    value = str(code or "").strip().lower()
    if not value:
        return None
    if value in {"budget_overrun"}:
        return "critical"
    if value in {"po_missing_reference", "po_amount_mismatch", "policy_validation_failed"}:
        return "high"
    if value in {"missing_budget_context"}:
        return "medium"
    return "medium"


def _derive_exception_from_metadata(
    metadata: Dict[str, Any],
    budget_summary: Dict[str, Any],
) -> Dict[str, Optional[str]]:
    code = _normalize_exception_code(metadata.get("exception_code"))
    severity = _normalize_exception_severity(metadata.get("exception_severity"))

    gate = _parse_json(metadata.get("validation_gate"))
    gate_reasons = gate.get("reasons") if isinstance(gate.get("reasons"), list) else []
    if not code:
        reason_codes = gate.get("reason_codes") if isinstance(gate.get("reason_codes"), list) else []
        if reason_codes:
            code = _normalize_exception_code(reason_codes[0])

    if not severity and code and gate_reasons:
        for reason in gate_reasons:
            if not isinstance(reason, dict):
                continue
            reason_code = _normalize_exception_code(reason.get("code"))
            if reason_code == code:
                severity = _normalize_exception_severity(reason.get("severity"))
                if severity:
                    break

    budget_status = str(budget_summary.get("status") or "").strip().lower()
    if not code and budget_summary.get("requires_decision"):
        code = "budget_overrun"
    if not severity and budget_summary.get("requires_decision"):
        severity = "critical" if budget_status == "exceeded" else "high"

    if not severity:
        severity = _default_severity_for_exception(code)
    return {"code": code, "severity": severity}


def _summarize_budget_context(metadata: Dict[str, Any], approvals: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    checks = _normalize_budget_checks(metadata.get("budget_impact"))
    if not checks:
        checks = _normalize_budget_checks(metadata.get("budget"))
    if not checks:
        checks = _normalize_budget_checks(metadata.get("budget_check_result"))

    if not checks and approvals:
        for approval in approvals:
            payload = _parse_json(approval.get("decision_payload"))
            checks = _normalize_budget_checks(payload.get("budget_impact"))
            if checks:
                break
            checks = _normalize_budget_checks(payload.get("budget"))
            if checks:
                break

    status = str(metadata.get("budget_status") or metadata.get("status") or "unknown").strip().lower()
    highest_rank = _budget_status_rank(status)
    exceeded_count = 0
    critical_count = 0
    warning_count = 0
    rows: List[Dict[str, Any]] = []

    for check in checks:
        row_status = str(check.get("after_approval_status") or check.get("status") or "unknown").strip().lower()
        rank = _budget_status_rank(row_status)
        if rank > highest_rank:
            highest_rank = rank
            status = row_status
        if row_status == "exceeded":
            exceeded_count += 1
        elif row_status == "critical":
            critical_count += 1
        elif row_status == "warning":
            warning_count += 1

        budget_amount = _safe_float(check.get("budget_amount"))
        after_approval = _safe_float(check.get("after_approval"))
        remaining = check.get("remaining")
        if remaining is None:
            remaining = budget_amount - after_approval
        rows.append(
            {
                "name": check.get("budget_name") or "Budget",
                "status": row_status,
                "percent_after_approval": _safe_float(check.get("after_approval_percent") or check.get("percent_used")),
                "invoice_amount": _safe_float(check.get("invoice_amount")),
                "remaining": _safe_float(remaining),
                "warning_message": check.get("warning_message"),
            }
        )

    requires_decision = status in {"critical", "exceeded"}
    return {
        "status": status or "unknown",
        "requires_decision": requires_decision,
        "critical_count": critical_count,
        "exceeded_count": exceeded_count,
        "warning_count": warning_count,
        "checks": rows,
    }


def _build_primary_source(item: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    for source in sources:
        source_type = str(source.get("source_type") or "").strip().lower()
        source_ref = str(source.get("source_ref") or "").strip()
        if source_type == "gmail_thread" and source_ref:
            return {"thread_id": source_ref, "message_id": item.get("message_id")}
        if source_type == "gmail_message" and source_ref:
            return {"thread_id": item.get("thread_id"), "message_id": source_ref}
    return {"thread_id": item.get("thread_id"), "message_id": item.get("message_id")}


def build_worklist_item(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item or {})
    metadata = _parse_json(payload.get("metadata"))
    sources = db.list_ap_item_sources(payload.get("id"))

    # Preserve legacy behavior when source links do not exist yet.
    if not sources:
        if payload.get("thread_id"):
            sources.append(
                {
                    "source_type": "gmail_thread",
                    "source_ref": payload.get("thread_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )
        if payload.get("message_id"):
            sources.append(
                {
                    "source_type": "gmail_message",
                    "source_ref": payload.get("message_id"),
                    "subject": payload.get("subject"),
                    "sender": payload.get("sender"),
                    "detected_at": payload.get("created_at"),
                    "metadata": {},
                }
            )

    meta_source_count = metadata.get("source_count")
    try:
        parsed_meta_source_count = int(meta_source_count) if meta_source_count is not None else 0
    except (TypeError, ValueError):
        parsed_meta_source_count = 0
    payload["source_count"] = max(parsed_meta_source_count, len(sources))
    payload["primary_source"] = _build_primary_source(payload, sources)
    payload["merge_reason"] = metadata.get("merge_reason")
    payload["has_context_conflict"] = bool(
        metadata.get("has_context_conflict") or metadata.get("context_conflict")
    )
    budget_summary = _summarize_budget_context(metadata)
    existing_exception = {
        "code": _normalize_exception_code(payload.get("exception_code")),
        "severity": _normalize_exception_severity(payload.get("exception_severity")),
    }
    derived_exception = _derive_exception_from_metadata(metadata, budget_summary)
    payload["exception_code"] = existing_exception["code"] or derived_exception["code"]
    payload["exception_severity"] = (
        existing_exception["severity"]
        or derived_exception["severity"]
        or _default_severity_for_exception(payload.get("exception_code"))
    )
    payload["budget_status"] = (
        metadata.get("budget_status")
        or payload.get("budget_status")
        or budget_summary.get("status")
    )
    payload["budget_requires_decision"] = bool(budget_summary.get("requires_decision"))
    payload["risk_signals"] = metadata.get("risk_signals") or {}
    payload["source_ranking"] = metadata.get("source_ranking") or {}
    payload["navigator"] = metadata.get("navigator") or {}
    payload["conflict_actions"] = metadata.get("conflict_actions") if isinstance(metadata.get("conflict_actions"), list) else []
    if metadata.get("priority_score") is not None:
        payload["priority_score"] = metadata.get("priority_score")
    elif hasattr(db, "_worklist_priority_score"):
        try:
            payload["priority_score"] = db._worklist_priority_score(payload)  # type: ignore[attr-defined]
        except Exception:
            pass
    return payload


def _require_item(db: ClearledgrDB, ap_item_id: str) -> Dict[str, Any]:
    item = db.get_ap_item(ap_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="ap_item_not_found")
    return item


def _build_context_payload(db: ClearledgrDB, item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _parse_json(item.get("metadata"))
    sources = db.list_ap_item_sources(item["id"])
    approvals = db.list_approvals_by_item(item["id"], limit=20)
    audit_events = db.list_ap_audit_events(item["id"])
    now = datetime.now(timezone.utc)

    multi_system, discovered_sources = build_multi_system_context(
        item=item,
        metadata=metadata,
        sources=sources,
        audit_events=audit_events,
    )
    for discovered in discovered_sources:
        try:
            db.link_ap_item_source(discovered)
        except Exception:
            # Keep context rendering resilient if source persistence fails.
            pass

    # Reload after discovery so source distribution/coverage reflects connector links.
    sources = db.list_ap_item_sources(item["id"])

    source_types: Dict[str, int] = {}
    for source in sources:
        source_type = str(source.get("source_type") or "unknown")
        source_types[source_type] = source_types.get(source_type, 0) + 1
    distribution = ", ".join(f"{k}:{v}" for k, v in sorted(source_types.items()))

    organization_id = str(item.get("organization_id") or "default")
    vendor_name = str(item.get("vendor_name") or "").strip()
    vendor_key = vendor_name.lower()
    vendor_items = []
    if vendor_key:
        for candidate in db.list_ap_items(organization_id, limit=5000):
            candidate_vendor = str(candidate.get("vendor_name") or "").strip().lower()
            if candidate_vendor == vendor_key:
                vendor_items.append(candidate)
    vendor_total_spend = round(sum(_safe_float(entry.get("amount")) for entry in vendor_items), 2)
    vendor_open_count = sum(
        1
        for entry in vendor_items
        if str(entry.get("state") or "").strip().lower()
        in {"received", "validated", "needs_info", "needs_approval", "pending_approval", "approved", "ready_to_post"}
    )
    vendor_posted_count = sum(
        1
        for entry in vendor_items
        if str(entry.get("state") or "").strip().lower() in {"closed", "posted_to_erp"}
    )

    browser_events = [
        event for event in audit_events if str(event.get("event_type") or "").startswith("browser_")
    ]
    recent_browser_events: List[Dict[str, Any]] = []
    for event in browser_events[-10:]:
        payload = event.get("payload_json") or {}
        request_payload = payload.get("request") if isinstance(payload, dict) else {}
        recent_browser_events.append(
            {
                "event_id": event.get("id"),
                "ts": event.get("ts"),
                "status": payload.get("status") if isinstance(payload, dict) else None,
                "tool_name": (request_payload or {}).get("tool_name"),
                "command_id": payload.get("command_id") if isinstance(payload, dict) else None,
                "result": payload.get("result") if isinstance(payload, dict) else None,
            }
        )

    payment_portals = [
        source for source in sources if str(source.get("source_type") or "").lower() == "portal"
    ]
    procurement = [
        source for source in sources if str(source.get("source_type") or "").lower() == "procurement"
    ]
    dms_documents = [
        source for source in sources if str(source.get("source_type") or "").lower() == "dms"
    ]
    card_sources = [
        source
        for source in sources
        if str(source.get("source_type") or "").lower() in {"card_statement", "credit_card", "card"}
    ]
    bank_sources = [
        source for source in sources if str(source.get("source_type") or "").lower() == "bank"
    ]
    payroll_sources = [
        source for source in sources if str(source.get("source_type") or "").lower() == "payroll"
    ]
    spreadsheet_sources = [
        source
        for source in sources
        if str(source.get("source_type") or "").lower() in {"spreadsheet", "sheets"}
    ]

    latest_approval = approvals[0] if approvals else None
    latest_approval_payload = _parse_json(latest_approval.get("decision_payload")) if latest_approval else {}
    thread_preview = latest_approval_payload.get("thread_preview")
    if not isinstance(thread_preview, list):
        thread_preview = []
    budget_summary = _summarize_budget_context(metadata, approvals)
    approval_budget = budget_summary if budget_summary.get("checks") else (_parse_json(latest_approval_payload.get("budget")) or budget_summary)
    teams_context = _parse_json(metadata.get("teams")) or {}
    if approvals:
        for approval in approvals:
            source_channel = str(approval.get("source_channel") or "").strip().lower()
            if source_channel not in {"teams", "microsoft_teams", "ms_teams"}:
                continue
            teams_payload = _parse_json(approval.get("decision_payload"))
            merged = dict(teams_context)
            merged.setdefault("channel", approval.get("channel_id"))
            merged.setdefault("message_id", approval.get("message_ts"))
            merged.setdefault("state", approval.get("status"))
            if teams_payload.get("decision"):
                merged["last_action"] = teams_payload.get("decision")
            if approval.get("approved_by"):
                merged["updated_by"] = approval.get("approved_by")
            elif approval.get("rejected_by"):
                merged["updated_by"] = approval.get("rejected_by")
            if approval.get("rejection_reason"):
                merged["reason"] = approval.get("rejection_reason")
            teams_context = merged
            break

    erp_reference = item.get("erp_reference")
    connector_available = bool(erp_reference or metadata.get("erp_connector_available") or metadata.get("erp"))
    multi_system_summary = multi_system.get("summary") if isinstance(multi_system.get("summary"), dict) else {}
    connected_systems = (
        list(multi_system_summary.get("connected_systems") or [])
        if isinstance(multi_system_summary.get("connected_systems"), list)
        else []
    )
    summary_lines: List[str] = []
    if vendor_name:
        summary_lines.append(
            f"{vendor_name}: ${vendor_total_spend:,.2f} total tracked spend "
            f"({vendor_open_count} open, {vendor_posted_count} posted)."
        )
    if connected_systems:
        summary_lines.append(f"Connected systems: {', '.join(connected_systems)}.")
    if budget_summary.get("status") in {"critical", "exceeded"}:
        summary_lines.append(f"Budget status is {budget_summary.get('status')}; approval decision is required.")
    if metadata.get("has_context_conflict"):
        summary_lines.append("Context conflict detected; review merge/source evidence before posting.")
    if not summary_lines:
        summary_lines.append("Context is available. Review linked sources and proceed with approval controls.")
    summary_text = " ".join(summary_lines)

    context = {
        "schema_version": "2.0",
        "ap_item_id": item.get("id"),
        "generated_at": now.isoformat(),
        "freshness": {
            "age_seconds": 0,
            "is_stale": False,
        },
        "source_quality": {
            "distribution": distribution or "none",
            "total_sources": len(sources),
        },
        "email": {
            "source_count": len(sources),
            "sources": sources,
        },
        "web": {
            "browser_event_count": len(browser_events),
            "recent_browser_events": recent_browser_events[-5:],
            "related_portals": payment_portals,
            "payment_portals": payment_portals,
            "procurement": procurement,
            "dms_documents": dms_documents,
            "card_statements": _parse_json(multi_system.get("card_statements")).get("matched_transactions")
            if isinstance(multi_system.get("card_statements"), dict)
            else [],
            "bank_transactions": _parse_json(multi_system.get("bank")).get("matched_transactions") if isinstance(multi_system.get("bank"), dict) else [],
            "spreadsheets": _parse_json(multi_system.get("spreadsheets")).get("references") if isinstance(multi_system.get("spreadsheets"), dict) else [],
            "connector_coverage": {
                "payment_portal": bool(payment_portals),
                "procurement": bool(procurement or _parse_json(multi_system.get("summary")).get("has_procurement")),
                "dms": bool(dms_documents),
                "card_statements": bool(
                    card_sources or _parse_json(multi_system.get("summary")).get("has_card_statements")
                ),
                "bank": bool(bank_sources or _parse_json(multi_system.get("summary")).get("has_bank")),
                "payroll": bool(payroll_sources or _parse_json(multi_system.get("summary")).get("has_payroll")),
                "spreadsheets": bool(
                    spreadsheet_sources or _parse_json(multi_system.get("summary")).get("has_spreadsheets")
                ),
            },
        },
        "approvals": {
            "count": len(approvals),
            "latest": latest_approval,
            "slack": {
                "thread_preview": thread_preview[:5],
            },
            "teams": teams_context,
            "budget": approval_budget,
            "payroll": multi_system.get("payroll") if isinstance(multi_system.get("payroll"), dict) else {},
            "aggregated": {
                "vendor_name": vendor_name or None,
                "vendor_spend_to_date": vendor_total_spend,
                "vendor_open_invoices": int(vendor_open_count),
                "vendor_posted_invoices": int(vendor_posted_count),
                "connected_systems": connected_systems,
                "source_count": len(sources),
            },
        },
        "erp": {
            "state": item.get("state"),
            "erp_reference": erp_reference,
            "erp_posted_at": item.get("erp_posted_at"),
            "connector_available": connector_available,
        },
        "po_match": metadata.get("po_match") or metadata.get("po_match_result") or {},
        "budget": budget_summary,
        "risk_signals": metadata.get("risk_signals") or {},
        "bank": multi_system.get("bank") if isinstance(multi_system.get("bank"), dict) else {},
        "card_statements": multi_system.get("card_statements")
        if isinstance(multi_system.get("card_statements"), dict)
        else {},
        "procurement": multi_system.get("procurement") if isinstance(multi_system.get("procurement"), dict) else {},
        "payroll": multi_system.get("payroll") if isinstance(multi_system.get("payroll"), dict) else {},
        "spreadsheets": multi_system.get("spreadsheets") if isinstance(multi_system.get("spreadsheets"), dict) else {},
        "dms_documents": multi_system.get("dms_documents")
        if isinstance(multi_system.get("dms_documents"), dict)
        else {},
        "multi_system": multi_system.get("summary") if isinstance(multi_system.get("summary"), dict) else {},
        "summary": {
            "text": summary_text,
            "highlights": summary_lines,
            "connected_systems": connected_systems,
            "vendor_spend_to_date": vendor_total_spend,
            "vendor_open_invoices": int(vendor_open_count),
        },
    }
    return context


@router.get("/metrics/aggregation")
def get_ap_aggregation_metrics(
    organization_id: str = Query(default="default"),
    limit: int = Query(default=10000, ge=100, le=50000),
    vendor_limit: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    """AP aggregation metrics for embedded approvals and ops consumers."""
    db = get_db()
    metrics = db.get_ap_aggregation_metrics(
        organization_id=organization_id,
        limit=limit,
        vendor_limit=vendor_limit,
    )
    return {"metrics": metrics}


@router.get("/{ap_item_id}/audit")
def get_ap_item_audit(ap_item_id: str, browser_only: bool = Query(False)) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    events = db.list_ap_audit_events(ap_item_id)
    if browser_only:
        events = [event for event in events if str(event.get("event_type") or "").startswith("browser_")]
    return {"events": events}


@router.get("/{ap_item_id}/sources")
def get_ap_item_sources(ap_item_id: str) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    sources = db.list_ap_item_sources(ap_item_id)
    return {"sources": sources, "source_count": len(sources)}


@router.post("/{ap_item_id}/sources/link")
def link_ap_item_source(ap_item_id: str, request: LinkSourceRequest) -> Dict[str, Any]:
    db = get_db()
    _require_item(db, ap_item_id)
    source = db.link_ap_item_source(
        {
            "ap_item_id": ap_item_id,
            "source_type": request.source_type,
            "source_ref": request.source_ref,
            "subject": request.subject,
            "sender": request.sender,
            "detected_at": request.detected_at,
            "metadata": request.metadata or {},
        }
    )
    return {"source": source}


@router.get("/{ap_item_id}/context")
def get_ap_item_context(ap_item_id: str, refresh: bool = Query(False)) -> Dict[str, Any]:
    db = get_db()
    item = _require_item(db, ap_item_id)

    if not refresh:
        cached = db.get_ap_item_context_cache(ap_item_id)
        if cached and isinstance(cached.get("context_json"), dict):
            context = dict(cached.get("context_json") or {})
            schema_version = str(context.get("schema_version") or "")
            if not schema_version.startswith("2."):
                context = {}
            if context:
                updated_at = _parse_iso(cached.get("updated_at"))
                if updated_at:
                    age_seconds = max(0, int((datetime.now(timezone.utc) - updated_at).total_seconds()))
                    freshness = context.get("freshness") if isinstance(context.get("freshness"), dict) else {}
                    freshness["age_seconds"] = age_seconds
                    freshness["is_stale"] = age_seconds > 300
                    context["freshness"] = freshness
                return context

    context = _build_context_payload(db, item)
    db.upsert_ap_item_context_cache(ap_item_id, context)
    return context


@router.post("/{ap_item_id}/merge")
def merge_ap_items(ap_item_id: str, request: MergeItemsRequest) -> Dict[str, Any]:
    db = get_db()
    target = _require_item(db, ap_item_id)
    source = _require_item(db, request.source_ap_item_id)

    if target.get("id") == source.get("id"):
        raise HTTPException(status_code=400, detail="cannot_merge_same_item")
    if str(target.get("organization_id") or "default") != str(source.get("organization_id") or "default"):
        raise HTTPException(status_code=400, detail="organization_mismatch")

    moved_count = 0
    for source_link in db.list_ap_item_sources(source["id"]):
        moved = db.move_ap_item_source(
            from_ap_item_id=source["id"],
            to_ap_item_id=target["id"],
            source_type=source_link.get("source_type"),
            source_ref=source_link.get("source_ref"),
        )
        if moved:
            moved_count += 1

    # Preserve legacy source pointers as explicit links when present.
    if source.get("thread_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_thread",
                "source_ref": source.get("thread_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )
    if source.get("message_id"):
        db.link_ap_item_source(
            {
                "ap_item_id": target["id"],
                "source_type": "gmail_message",
                "source_ref": source.get("message_id"),
                "subject": source.get("subject"),
                "sender": source.get("sender"),
                "detected_at": source.get("created_at"),
                "metadata": {"merge_origin": source.get("id")},
            }
        )

    target_meta = _parse_json(target.get("metadata"))
    merge_history = target_meta.get("merge_history")
    if not isinstance(merge_history, list):
        merge_history = []
    merge_history.append(
        {
            "source_ap_item_id": source["id"],
            "reason": request.reason,
            "actor_id": request.actor_id,
            "merged_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    target_meta["merge_history"] = merge_history
    target_meta["merge_reason"] = request.reason
    target_meta["has_context_conflict"] = False
    target_meta["source_count"] = len(db.list_ap_item_sources(target["id"]))
    db.update_ap_item(target["id"], metadata=target_meta)

    source_meta = _parse_json(source.get("metadata"))
    source_meta["merged_into"] = target["id"]
    source_meta["merge_reason"] = request.reason
    db.update_ap_item(source["id"], state="merged", metadata=source_meta)

    db.append_ap_audit_event(
        {
            "ap_item_id": target["id"],
            "event_type": "ap_item_merged",
            "actor_type": "user",
            "actor_id": request.actor_id,
            "payload_json": {
                "target_ap_item_id": target["id"],
                "source_ap_item_id": source["id"],
                "reason": request.reason,
                "moved_sources": moved_count,
            },
            "organization_id": target.get("organization_id") or "default",
            "source": "ap_items_api",
            "decision_reason": request.reason,
            "idempotency_key": f"merge:{target['id']}:{source['id']}",
        }
    )

    return {
        "status": "merged",
        "target_ap_item_id": target["id"],
        "source_ap_item_id": source["id"],
        "moved_sources": moved_count,
    }


@router.post("/{ap_item_id}/split")
def split_ap_item(ap_item_id: str, request: SplitItemRequest) -> Dict[str, Any]:
    db = get_db()
    parent = _require_item(db, ap_item_id)
    if not request.sources:
        raise HTTPException(status_code=400, detail="sources_required")

    created_items: List[Dict[str, Any]] = []
    parent_meta = _parse_json(parent.get("metadata"))
    now = datetime.now(timezone.utc).isoformat()

    for source in request.sources:
        current_sources = db.list_ap_item_sources(parent["id"], source_type=source.source_type)
        current = next((row for row in current_sources if row.get("source_ref") == source.source_ref), None)
        if not current:
            continue

        split_payload = {
            "invoice_key": f"{parent.get('invoice_key') or parent['id']}#split#{source.source_type}:{source.source_ref}",
            "thread_id": parent.get("thread_id"),
            "message_id": parent.get("message_id"),
            "subject": current.get("subject") or parent.get("subject"),
            "sender": current.get("sender") or parent.get("sender"),
            "vendor_name": parent.get("vendor_name"),
            "amount": parent.get("amount"),
            "currency": parent.get("currency") or "USD",
            "invoice_number": parent.get("invoice_number"),
            "invoice_date": parent.get("invoice_date"),
            "due_date": parent.get("due_date"),
            "state": "needs_info",
            "confidence": parent.get("confidence") or 0,
            "approval_required": bool(parent.get("approval_required", True)),
            "organization_id": parent.get("organization_id") or "default",
            "user_id": parent.get("user_id"),
            "metadata": {
                **parent_meta,
                "split_from_ap_item_id": parent["id"],
                "split_reason": request.reason,
                "split_actor_id": request.actor_id,
                "split_source": {"source_type": source.source_type, "source_ref": source.source_ref},
                "split_at": now,
            },
        }
        child = db.create_ap_item(split_payload)
        db.move_ap_item_source(
            from_ap_item_id=parent["id"],
            to_ap_item_id=child["id"],
            source_type=source.source_type,
            source_ref=source.source_ref,
        )

        if source.source_type == "gmail_thread":
            db.update_ap_item(child["id"], thread_id=source.source_ref)
        if source.source_type == "gmail_message":
            db.update_ap_item(child["id"], message_id=source.source_ref)

        db.append_ap_audit_event(
            {
                "ap_item_id": child["id"],
                "event_type": "ap_item_split_created",
                "actor_type": "user",
                "actor_id": request.actor_id,
                "payload_json": {
                    "parent_ap_item_id": parent["id"],
                    "source_type": source.source_type,
                    "source_ref": source.source_ref,
                    "reason": request.reason,
                },
                "organization_id": parent.get("organization_id") or "default",
                "source": "ap_items_api",
            }
        )
        created_items.append(child)

    if not created_items:
        raise HTTPException(status_code=400, detail="no_sources_split")

    parent_meta["source_count"] = len(db.list_ap_item_sources(parent["id"]))
    db.update_ap_item(parent["id"], metadata=parent_meta)

    return {
        "status": "split",
        "parent_ap_item_id": parent["id"],
        "created_items": [build_worklist_item(db, item) for item in created_items],
    }
