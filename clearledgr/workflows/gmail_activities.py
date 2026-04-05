"""Async activity helpers used by Gmail extension and webhook flows.

These activities provide stable async wrappers around the AP classifier/parser
and lightweight matching/escalation helpers.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from clearledgr.core.database import get_db
from clearledgr.services.ap_classifier import classify_ap_email
from clearledgr.services.email_parser import parse_email
from clearledgr.services.fuzzy_matching import vendor_similarity
from clearledgr.services.slack_api import SlackAPIClient, resolve_slack_runtime
from clearledgr.services.slack_notifications import send_with_retry

logger = logging.getLogger(__name__)


def _compact_attachment_evidence(raw_attachments: Any) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    if not isinstance(raw_attachments, list):
        return evidence
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        text_excerpt = str(raw.get("content_text") or "").strip()
        if len(text_excerpt) > 1200:
            text_excerpt = f"{text_excerpt[:1200]}..."
        extraction = raw.get("extraction") if isinstance(raw.get("extraction"), dict) else None
        entry = {
            "name": raw.get("name"),
            "type": raw.get("type"),
            "content_type": raw.get("content_type"),
            "parsed": bool(raw.get("parsed")),
            "requires_ocr": bool(raw.get("requires_ocr")),
            "has_text": bool(text_excerpt),
            "text_excerpt": text_excerpt or None,
            "extraction": extraction,
        }
        evidence.append({key: value for key, value in entry.items() if value not in (None, "", [], {})})
    return evidence


def _org_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("organization_id") or "default")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_confidence_pct(value: Any) -> float:
    raw = _safe_float(value, 0.0)
    if 0.0 <= raw <= 1.0:
        return raw * 100.0
    return raw


def _parse_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _amount_from_extraction(extraction: Dict[str, Any]) -> float:
    return _safe_float(
        extraction.get("amount")
        if extraction.get("amount") is not None
        else extraction.get("total_amount"),
        default=0.0,
    )


def _detect_discount_opportunity(
    amount: float, payment_terms: Any, invoice_date: Any, currency: str,
) -> Optional[Dict[str, Any]]:
    """Detect early payment discount opportunity from payment terms."""
    try:
        from clearledgr.services.discount_optimizer import calculate_discount_opportunity
        terms_str = str(payment_terms or "").strip()
        if not terms_str or amount <= 0:
            return None
        return calculate_discount_opportunity(
            amount=amount,
            payment_terms=terms_str,
            invoice_date=str(invoice_date or ""),
            currency=currency,
        )
    except Exception:
        return None


def _normalize_email_type(raw_type: str) -> str:
    normalized = str(raw_type or "").strip().upper()
    from clearledgr.services.document_routing import VALID_DOCUMENT_TYPES
    if normalized.lower() in VALID_DOCUMENT_TYPES:
        return normalized
    return "NOISE"


def _normalize_date(value: Any) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return raw


def _score_bank_candidate(
    *,
    invoice_amount: float,
    invoice_vendor: str,
    invoice_number: str,
    candidate: Dict[str, Any],
) -> float:
    amount_score = 0.0
    candidate_amount = _safe_float(candidate.get("amount"), 0.0)
    if invoice_amount > 0 and candidate_amount > 0:
        diff_ratio = abs(candidate_amount - invoice_amount) / max(invoice_amount, 1.0)
        amount_score = max(0.0, 1.0 - diff_ratio)

    vendor_score = 0.0
    candidate_vendor = str(candidate.get("vendor") or candidate.get("description") or "").strip()
    if invoice_vendor and candidate_vendor:
        vendor_score = vendor_similarity(invoice_vendor, candidate_vendor)

    ref_score = 0.0
    if invoice_number:
        ref_text = str(candidate.get("reference") or "").strip().lower()
        if ref_text and invoice_number.lower() in ref_text:
            ref_score = 1.0

    # Weighted to favor amount fit first, then vendor similarity.
    return (amount_score * 0.6) + (vendor_score * 0.3) + (ref_score * 0.1)


def _serialize_txn(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    if hasattr(candidate, "to_dict"):
        try:
            return dict(candidate.to_dict())
        except Exception:
            pass
    try:
        return dict(vars(candidate))
    except Exception:
        return {}


async def classify_email_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Classify an email into AP categories (invoice/payment_request/noise)."""
    subject = str(payload.get("subject") or "")
    sender = str(payload.get("sender") or "")
    snippet = str(payload.get("snippet") or "")
    body = str(payload.get("body") or "")
    attachments = payload.get("attachments") or []

    result = classify_ap_email(
        subject=subject,
        sender=sender,
        snippet=snippet,
        body=body,
        attachments=attachments,
    )
    return {
        "type": _normalize_email_type(result.get("type") or ""),
        "confidence": _safe_float(result.get("confidence"), 0.0),
        "reason": result.get("reason") or "",
        "method": result.get("method") or "rules",
    }


async def extract_email_data_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract AP-relevant fields from email body and attachments."""
    subject = str(payload.get("subject") or "")
    sender = str(payload.get("sender") or "")
    snippet = str(payload.get("snippet") or "")
    body = str(payload.get("body") or "")
    attachments = payload.get("attachments") or []

    parsed = parse_email(
        subject=subject,
        body=body or snippet,
        sender=sender,
        attachments=attachments,
        organization_id=str(payload.get("organization_id") or "default"),
        thread_id=payload.get("thread_id"),
    )
    parsed = parsed if isinstance(parsed, dict) else {}

    # parse_email may return primary_amount and amounts list.
    amount = _safe_float(parsed.get("primary_amount"), 0.0)
    if amount <= 0:
        amounts = parsed.get("amounts") or []
        if isinstance(amounts, list) and amounts:
            top = amounts[0] if isinstance(amounts[0], dict) else {"value": amounts[0]}
            amount = _safe_float(top.get("value"), 0.0)

    currency = str(parsed.get("currency") or "").strip().upper()
    if not currency:
        amounts = parsed.get("amounts") or []
        if isinstance(amounts, list) and amounts and isinstance(amounts[0], dict):
            currency = str(amounts[0].get("currency") or "").strip().upper()
    if not currency:
        currency = "USD"

    invoice_number = str(parsed.get("primary_invoice") or "").strip()
    if not invoice_number:
        invoice_number = str(parsed.get("invoice_number") or "").strip()

    due_date = _normalize_date(parsed.get("due_date") or parsed.get("primary_date"))
    vendor = str(parsed.get("vendor") or "").strip()
    if vendor.lower() in {"unknown", "unknown vendor", "n/a", "na", "none"}:
        vendor = ""

    confidence = _safe_float(parsed.get("confidence"), 0.0)
    field_confidences = parsed.get("field_confidences")
    if not isinstance(field_confidences, dict):
        field_confidences = {}

    return {
        "vendor": vendor or None,
        "amount": amount,
        "total_amount": amount,
        "currency": currency,
        "invoice_number": invoice_number or None,
        "due_date": due_date,
        "confidence": confidence,
        "document_type": parsed.get("document_type") or parsed.get("email_type"),
        "email_type": parsed.get("email_type"),
        "invoice_date": _normalize_date(parsed.get("invoice_date")),
        "field_confidences": field_confidences,
        "reasoning_summary": str(parsed.get("reasoning_summary") or "").strip() or None,
        "payment_processor": str(parsed.get("payment_processor") or "").strip() or None,
        "extraction_method": str(parsed.get("extraction_method") or "").strip() or None,
        "extraction_model": str(parsed.get("extraction_model") or "").strip() or None,
        "primary_source": str(parsed.get("primary_source") or "email").strip() or "email",
        "field_provenance": parsed.get("field_provenance") if isinstance(parsed.get("field_provenance"), dict) else {},
        "field_evidence": parsed.get("field_evidence") if isinstance(parsed.get("field_evidence"), dict) else {},
        "source_conflicts": parsed.get("source_conflicts") if isinstance(parsed.get("source_conflicts"), list) else [],
        "requires_extraction_review": bool(parsed.get("requires_extraction_review")),
        "conflict_actions": parsed.get("conflict_actions") if isinstance(parsed.get("conflict_actions"), list) else [],
        "attachment_count": int(parsed.get("attachment_count") or len(payload.get("attachments") or [])),
        "invoice_count": int(parsed.get("invoice_count") or 1),
        "multiple_invoices": bool(parsed.get("multiple_invoices")),
        "invoices": parsed.get("invoices") if isinstance(parsed.get("invoices"), list) else [],
        "attachment_names": [
            str(att.get("filename") or att.get("name") or "").strip()
            for att in (payload.get("attachments") or [])
            if isinstance(att, dict) and str(att.get("filename") or att.get("name") or "").strip()
        ],
        # Early payment discount opportunity
        "discount_opportunity": _detect_discount_opportunity(
            amount=amount, payment_terms=parsed.get("payment_terms"),
            invoice_date=_normalize_date(parsed.get("invoice_date")), currency=currency,
        ),
        "raw_parser": {
            "invoice_numbers": parsed.get("invoice_numbers") or [],
            "dates": parsed.get("dates") or [],
            "has_invoice_attachment": bool(parsed.get("has_invoice_attachment")),
            "has_statement_attachment": bool(parsed.get("has_statement_attachment")),
            "extraction_method": parsed.get("extraction_method"),
            "primary_source": parsed.get("primary_source"),
            "field_provenance": parsed.get("field_provenance") if isinstance(parsed.get("field_provenance"), dict) else {},
            "field_evidence": parsed.get("field_evidence") if isinstance(parsed.get("field_evidence"), dict) else {},
            "source_conflicts": parsed.get("source_conflicts") if isinstance(parsed.get("source_conflicts"), list) else [],
            "parsed_attachments": _compact_attachment_evidence(parsed.get("attachments") or []),
        },
    }


async def match_bank_feed_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Find candidate bank transaction matches for the extraction payload."""
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    org_id = _org_id(payload)
    invoice_amount = _amount_from_extraction(extraction)
    invoice_vendor = str(extraction.get("vendor") or "").strip()
    invoice_number = str(extraction.get("invoice_number") or "").strip()

    db = get_db()
    raw_candidates: List[Any] = []
    try:
        raw_candidates = db.get_transactions(
            organization_id=org_id,
            source="bank",
            limit=200,
        ) or []
    except Exception:
        logger.error("Bank candidate lookup unavailable for org=%s", org_id)
        raw_candidates = []

    scored: List[Dict[str, Any]] = []
    for raw in raw_candidates:
        candidate = _serialize_txn(raw)
        if not candidate:
            continue
        score = _score_bank_candidate(
            invoice_amount=invoice_amount,
            invoice_vendor=invoice_vendor,
            invoice_number=invoice_number,
            candidate=candidate,
        )
        scored.append(
            {
                "score": round(score, 4),
                "transaction": {
                    "id": candidate.get("id"),
                    "date": candidate.get("date"),
                    "amount": _safe_float(candidate.get("amount"), 0.0),
                    "currency": candidate.get("currency") or "USD",
                    "vendor": candidate.get("vendor"),
                    "reference": candidate.get("reference"),
                    "description": candidate.get("description"),
                    "status": candidate.get("status"),
                    "source": candidate.get("source"),
                },
            }
        )

    scored.sort(key=lambda row: row["score"], reverse=True)
    best = scored[0] if scored else None
    confidence = _safe_float((best or {}).get("score"), 0.0)
    matched = bool(best and confidence >= 0.7)

    return {
        "status": "matched" if matched else "no_match",
        "matched": matched,
        "confidence": round(confidence, 4),
        "match": (best or {}).get("transaction"),
        "candidates": scored[:5],
        "candidate_count": len(scored),
        "organization_id": org_id,
    }


async def match_erp_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run lightweight ERP-side checks: vendor profile + duplicate hints."""
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    org_id = _org_id(payload)
    db = get_db()

    vendor_name = str(extraction.get("vendor") or "").strip()
    invoice_number = str(extraction.get("invoice_number") or "").strip()
    po_number = str(extraction.get("po_number") or "").strip()
    amount = _amount_from_extraction(extraction)

    vendor_profile = {}
    if vendor_name:
        try:
            vendor_profile = db.get_vendor_profile(org_id, vendor_name) or {}
        except Exception:
            vendor_profile = {}

    duplicate = None
    if vendor_name and invoice_number:
        try:
            duplicate = db.get_ap_item_by_vendor_invoice(org_id, vendor_name, invoice_number)
        except Exception:
            duplicate = None

    duplicate_open = None
    if vendor_name and invoice_number:
        try:
            duplicate_open = db.get_open_ap_item_by_vendor_invoice(org_id, vendor_name, invoice_number)
        except Exception:
            duplicate_open = None

    gl_hint = str(
        extraction.get("gl_code")
        or vendor_profile.get("typical_gl_code")
        or ""
    ).strip()

    vendor_match_confidence = 0.0
    if vendor_name and vendor_profile:
        vendor_match_confidence = max(
            0.6,
            min(0.98, 0.65 + (_safe_int(vendor_profile.get("invoice_count"), 0) / 100.0)),
        )

    duplicate_signal = bool(duplicate or duplicate_open)
    status = "matched" if vendor_profile and not duplicate_signal else "partial" if vendor_profile or duplicate_signal else "no_match"

    return {
        "status": status,
        "organization_id": org_id,
        "vendor_match": {
            "matched": bool(vendor_profile),
            "vendor_name": vendor_name or None,
            "confidence": round(vendor_match_confidence, 4),
            "requires_po": bool(vendor_profile.get("requires_po")) if vendor_profile else False,
        },
        "duplicate_invoice": {
            "detected": duplicate_signal,
            "existing_ap_item_id": str((duplicate_open or duplicate or {}).get("id") or "") or None,
            "existing_state": (duplicate_open or duplicate or {}).get("state"),
        },
        "po_match": {
            "status": "provided" if po_number else "missing",
            "po_number": po_number or None,
        },
        "gl_suggestion": {
            "gl_code": gl_hint or None,
            "source": "vendor_profile" if vendor_profile.get("typical_gl_code") else ("extraction" if extraction.get("gl_code") else None),
        },
        "amount": amount,
    }


async def send_slack_notification_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send (or enqueue retry for) a Slack escalation notification."""
    org_id = _org_id(payload)
    channel = str(payload.get("channel") or payload.get("slack_channel") or "#finance-escalations")
    email_id = str(payload.get("email_id") or "")
    ap_item_id = str(payload.get("ap_item_id") or "") or None
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    confidence_result = payload.get("confidence_result") if isinstance(payload.get("confidence_result"), dict) else {}
    db = get_db()
    ap_item = {}
    if ap_item_id and hasattr(db, "get_ap_item"):
        ap_item = db.get_ap_item(ap_item_id) or {}
    if not ap_item and email_id and hasattr(db, "get_invoice_status"):
        ap_item = db.get_invoice_status(email_id) or {}
        ap_item_id = ap_item_id or str(ap_item.get("id") or "") or None

    vendor = str(extraction.get("vendor") or "Unknown")
    amount = _safe_float(extraction.get("amount"), 0.0)
    currency = str(extraction.get("currency") or "USD")
    confidence_pct = _normalize_confidence_pct(
        confidence_result.get("confidence_pct")
        if confidence_result.get("confidence_pct") is not None
        else extraction.get("confidence")
    )
    mismatches = confidence_result.get("mismatches") if isinstance(confidence_result.get("mismatches"), list) else []
    metadata = _parse_json_dict(ap_item.get("metadata"))
    requested_at = (
        _parse_iso_datetime(metadata.get("approval_requested_at"))
        or _parse_iso_datetime(ap_item.get("updated_at"))
        or _parse_iso_datetime(ap_item.get("created_at"))
    )
    hours_waiting = None
    if requested_at is not None:
        hours_waiting = max(
            0.1,
            round((datetime.now(timezone.utc) - requested_at).total_seconds() / 3600.0, 1),
        )

    mismatch_lines = []
    for mismatch in mismatches[:5]:
        message = str(mismatch.get("message") or "").strip()
        if message:
            mismatch_lines.append(f"- {message}")
            continue
        field = str(mismatch.get("field") or "field")
        extracted = str(mismatch.get("extracted") or "").strip()
        expected = str(mismatch.get("expected") or "").strip()
        if extracted or expected:
            mismatch_lines.append(f"- {field}: {extracted or 'n/a'} -> {expected or 'n/a'}")
        else:
            mismatch_lines.append(f"- {field} needs review")
    if not mismatch_lines:
        if hours_waiting is not None:
            mismatch_lines.append(f"- Approval has been waiting for {hours_waiting:.1f}h")
        else:
            mismatch_lines.append("- Manual review requested")
    mismatch_text = "\n".join(mismatch_lines)
    action_ref = email_id or ap_item_id or "unknown"

    text = (
        f"AP review required: {vendor} "
        f"({currency} {amount:,.2f})"
    )
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Invoice escalation"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Vendor:* {vendor}\n"
                    f"*Amount:* {currency} {amount:,.2f}\n"
                    f"*Confidence:* {confidence_pct:.1f}%\n"
                    f"*Email:* {email_id or 'n/a'}"
                    + (f"\n*Waiting:* {hours_waiting:.1f}h" if hours_waiting is not None else "")
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why this was escalated:*\n{mismatch_text}"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": f"approve_invoice_{action_ref}",
                    "value": action_ref,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": f"reject_invoice_{action_ref}",
                    "value": action_ref,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request info"},
                    "action_id": f"request_info_{action_ref}",
                    "value": action_ref,
                },
            ],
        },
    ]
    runtime = resolve_slack_runtime(org_id)
    slack_thread = db.get_slack_thread(email_id) if email_id and hasattr(db, "get_slack_thread") else None
    thread_channel = str((slack_thread or {}).get("channel_id") or "").strip()
    thread_ts = str((slack_thread or {}).get("thread_ts") or (slack_thread or {}).get("thread_id") or "").strip()
    cooldown_seconds = max(60, _safe_int(os.getenv("SLACK_ESCALATION_COOLDOWN_SECONDS"), 30 * 60))
    last_escalated_at = (
        _parse_iso_datetime(metadata.get("approval_last_escalated_at"))
        or _parse_iso_datetime(metadata.get("approval_last_slack_escalation_at"))
    )
    now = datetime.now(timezone.utc)

    if thread_ts and last_escalated_at and (now - last_escalated_at) <= timedelta(seconds=cooldown_seconds):
        return {
            "status": "deduped",
            "delivered": True,
            "deduped": True,
            "organization_id": org_id,
            "channel": thread_channel or channel,
            "email_id": email_id or None,
            "thread_ts": thread_ts,
            "threaded": True,
        }

    delivered = False
    delivered_channel = thread_channel or channel
    delivered_thread_ts = thread_ts or None
    if runtime.get("bot_token"):
        try:
            sent = await SlackAPIClient(bot_token=str(runtime.get("bot_token"))).send_message(
                channel=delivered_channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts or None,
                reply_broadcast=False,
                unfurl_links=False,
                unfurl_media=False,
            )
            delivered = True
            delivered_channel = sent.channel or delivered_channel
            delivered_thread_ts = thread_ts or sent.ts
            if email_id and hasattr(db, "save_slack_thread") and not thread_ts:
                db.save_slack_thread(
                    email_id,
                    channel_id=delivered_channel,
                    thread_ts=delivered_thread_ts or "",
                    thread_id=delivered_thread_ts or "",
                )
            elif email_id and hasattr(db, "update_slack_thread_status") and delivered_thread_ts:
                db.update_slack_thread_status(
                    email_id,
                    channel_id=delivered_channel,
                    thread_ts=delivered_thread_ts,
                    thread_id=delivered_thread_ts,
                )
        except Exception:
            delivered = False

    if not delivered:
        delivered = await send_with_retry(
            blocks=blocks,
            text=text,
            ap_item_id=ap_item_id,
            preferred_channel=channel,
            organization_id=org_id,
        )

    if delivered and ap_item_id and hasattr(db, "update_ap_item_metadata_merge"):
        db.update_ap_item_metadata_merge(
            ap_item_id,
            {
                "approval_last_slack_escalation_at": now.isoformat(),
                "approval_last_slack_escalation_channel": delivered_channel,
                "approval_last_slack_thread_ts": delivered_thread_ts,
            },
        )

    return {
        "status": "sent" if delivered else "queued_for_retry",
        "delivered": bool(delivered),
        "organization_id": org_id,
        "channel": delivered_channel,
        "email_id": email_id or None,
        "thread_ts": delivered_thread_ts,
        "threaded": bool(delivered_thread_ts),
    }


__all__ = [
    "classify_email_activity",
    "extract_email_data_activity",
    "match_bank_feed_activity",
    "match_erp_activity",
    "send_slack_notification_activity",
]
