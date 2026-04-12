"""
Gmail Pub/Sub Webhook Handler

Receives push notifications from Google Cloud Pub/Sub when new emails arrive.
This enables 24/7 autonomous email processing without requiring the browser to be open.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from html import escape
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from clearledgr.core.errors import safe_error
from clearledgr.core.auth import TokenData, get_current_user
from clearledgr.core.ap_confidence import evaluate_critical_field_confidence

from clearledgr.services.gmail_api import (
    GmailAPIClient,
    GmailWatchService,
    token_store,
    exchange_code_for_tokens,
)
from clearledgr.core.database import get_db
from clearledgr.core.models import FinanceEmail

logger = logging.getLogger(__name__)

_GENERIC_VENDOR_ALIASES = {
    "google",
    "stripe",
    "paypal",
    "square",
    "google workspace",
}


def _attachment_manifest(raw_attachments: Any, *, include_content: bool = False) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    if not isinstance(raw_attachments, list):
        return manifest
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        entry = {
            "name": str(raw.get("filename") or raw.get("name") or "").strip() or None,
            "content_type": str(
                raw.get("content_type")
                or raw.get("mime_type")
                or raw.get("mimeType")
                or ""
            ).strip()
            or None,
            "size": raw.get("size"),
        }
        if include_content:
            entry["has_content"] = bool(raw.get("content_base64") or raw.get("content_text"))
        manifest.append({key: value for key, value in entry.items() if value not in (None, "", [], {})})
    return manifest


def _merge_finance_email_metadata(existing: Optional[FinanceEmail], updates: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(getattr(existing, "metadata", {}) or {})
    for key, value in (updates or {}).items():
        if value in (None, "", [], {}):
            continue
        metadata[key] = value
    return metadata


def _conflict_blockers(raw_conflicts: Any) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not isinstance(raw_conflicts, list):
        return blockers
    for conflict in raw_conflicts:
        if not isinstance(conflict, dict) or not conflict.get("blocking"):
            continue
        field = str(conflict.get("field") or "").strip()
        if not field:
            continue
        blockers.append(
            {
                "field": field,
                "reason": str(conflict.get("reason") or "source_value_mismatch"),
                "severity": str(conflict.get("severity") or "high"),
                "sources": list((conflict.get("values") or {}).keys()),
                "preferred_source": str(conflict.get("preferred_source") or "attachment"),
                "values": conflict.get("values") or {},
            }
        )
    return blockers


def _build_extraction_review_gate(
    *,
    extraction: Dict[str, Any],
    amount: float,
    currency: str,
    invoice_number: Optional[str],
    due_date: Optional[str],
    confidence: float,
) -> Dict[str, Any]:
    gate = evaluate_critical_field_confidence(
        overall_confidence=confidence,
        field_values={
            "vendor": extraction.get("vendor"),
            "amount": amount,
            "invoice_number": invoice_number,
            "due_date": due_date,
        },
        field_confidences=extraction.get("field_confidences") if isinstance(extraction.get("field_confidences"), dict) else {},
    )
    blockers = list(gate.get("confidence_blockers") or [])
    blockers.extend(_conflict_blockers(extraction.get("source_conflicts")))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        key = (
            str(blocker.get("field") or "").strip(),
            str(blocker.get("reason") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    gate["confidence_blockers"] = deduped
    gate["requires_field_review"] = bool(deduped or extraction.get("requires_extraction_review"))
    gate["currency"] = currency
    return gate


def _lookup_latest_ap_item(db: Any, organization_id: str, *, thread_id: Optional[str], message_id: Optional[str]) -> Optional[Dict[str, Any]]:
    item = None
    if thread_id and hasattr(db, "get_ap_item_by_thread"):
        try:
            item = db.get_ap_item_by_thread(organization_id, thread_id)
        except Exception:
            item = None
    if not item and message_id and hasattr(db, "get_ap_item_by_message_id"):
        try:
            item = db.get_ap_item_by_message_id(organization_id, message_id)
        except Exception:
            item = None
    return item if isinstance(item, dict) else None


def _label_only_document_parse(message: Any) -> Dict[str, Any]:
    try:
        from clearledgr.services.email_parser import EmailParser

        parser = EmailParser()
        attachment_manifest = [
            {
                "name": str(raw.get("filename") or raw.get("name") or "").strip(),
                "filename": str(raw.get("filename") or raw.get("name") or "").strip(),
                "content_type": str(raw.get("content_type") or raw.get("mime_type") or raw.get("mimeType") or "").strip(),
            }
            for raw in (getattr(message, "attachments", None) or [])
            if isinstance(raw, dict)
        ]
        parsed = parser.parse_email(
            subject=getattr(message, "subject", "") or "",
            body=getattr(message, "body_text", "") or "",
            sender=getattr(message, "sender", "") or "",
            attachments=attachment_manifest,
        )
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.debug("Label-only finance document parse failed for %s: %s", getattr(message, "id", "unknown"), exc)
        return {}


def _label_only_document_type(message: Any, parsed: Optional[Dict[str, Any]] = None) -> Optional[str]:
    parsed = parsed if isinstance(parsed, dict) else {}
    subject_only = str(getattr(message, "subject", "") or "").lower()
    if re.search(r"\b(credit note|credit memo)\b", subject_only):
        return "credit_note"
    if re.search(r"\brefund\b", subject_only):
        return "refund"
    if re.search(r"\b(payment confirmation|payment received|payment processed|payment successful|payment completed)\b", subject_only):
        return "payment"
    parsed_type = str(parsed.get("email_type") or "").strip().lower()
    if parsed_type in {"payment", "receipt", "refund", "credit_note", "invoice", "payment_request", "statement"}:
        return parsed_type

    subject_and_body = " ".join(
        [
            str(getattr(message, "subject", "") or ""),
            str(getattr(message, "body_text", "") or ""),
            str(getattr(message, "snippet", "") or ""),
        ]
    ).lower()
    attachment_names = " ".join(
        str(raw.get("filename") or raw.get("name") or "").strip().lower()
        for raw in (getattr(message, "attachments", None) or [])
        if isinstance(raw, dict)
    )

    if re.search(r"\b(credit note|credit memo)\b", subject_and_body):
        return "credit_note"
    if re.search(r"\brefund\b", subject_and_body):
        return "refund"
    if re.search(r"\b(payment confirmation|payment received|payment processed|payment successful|payment completed)\b", subject_and_body):
        return "payment"
    if parsed.get("has_statement_attachment"):
        return "statement"
    if re.search(r"\b(bank|card|account)\s+statement\b", subject_and_body):
        return "statement"
    if "statement" in attachment_names:
        return "statement"
    return None


def _save_label_only_finance_email(
    db: Any,
    *,
    message: Any,
    user_id: str,
    organization_id: str,
    document_type: str,
    parsed: Optional[Dict[str, Any]] = None,
) -> FinanceEmail:
    parsed = parsed if isinstance(parsed, dict) else {}
    existing = db.get_finance_email_by_gmail_id(message.id) if hasattr(db, "get_finance_email_by_gmail_id") else None
    received_at = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
    payload = {
        "gmail_id": message.id,
        "subject": message.subject or "",
        "sender": message.sender or "",
        "received_at": received_at,
        "email_type": document_type,
        "confidence": float(parsed.get("confidence") or 0.8),
        "vendor": parsed.get("vendor") or None,
        "amount": parsed.get("amount"),
        "currency": str(parsed.get("currency") or "USD").strip().upper() or "USD",
        "invoice_number": parsed.get("invoice_number"),
        "status": "processed",
        "organization_id": organization_id,
        "user_id": user_id,
        "metadata": _merge_finance_email_metadata(
            existing,
            {
                "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
                "attachment_manifest": _attachment_manifest(getattr(message, "attachments", None) or []),
                "label_only_parse": parsed,
                "document_type": document_type,
                "email_type": document_type,
            },
        ),
    }
    existing_id = str(getattr(existing, "id", "") or "").strip()
    if existing_id:
        payload["id"] = existing_id
    return db.save_finance_email(FinanceEmail(**payload))


async def _sync_message_finance_labels(
    client: GmailAPIClient,
    *,
    user_id: str,
    organization_id: str,
    message_id: str,
    thread_id: Optional[str] = None,
    finance_email: Optional[Any] = None,
    ap_item: Optional[Dict[str, Any]] = None,
    document_type: Optional[str] = None,
    db: Optional[Any] = None,
) -> None:
    if not message_id:
        return
    db = db or get_db()
    row = ap_item or _lookup_latest_ap_item(
        db,
        organization_id,
        thread_id=thread_id,
        message_id=message_id,
    )
    record = finance_email
    if record is None and hasattr(db, "get_finance_email_by_gmail_id"):
        try:
            record = db.get_finance_email_by_gmail_id(message_id)
        except Exception:
            record = None

    await sync_finance_labels(
        client,
        message_id,
        ap_item=row,
        finance_email=record,
        document_type=document_type,
        user_email=user_id,
    )

router = APIRouter(prefix="/gmail", tags=["gmail"])

from clearledgr.services.gmail_labels import sync_finance_labels

_ORG_ADMIN_ROLES = {"admin", "owner", "api"}


def _is_prod_like_env() -> bool:
    return str(os.getenv("ENV", "dev")).strip().lower() in {"prod", "production", "stage", "staging"}


def _allow_unverified_push_in_prod() -> bool:
    raw = str(os.getenv("GMAIL_PUSH_ALLOW_UNVERIFIED_IN_PROD", "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalize_vendor_name(value: Any) -> str:
    vendor = str(value or "").strip()
    if vendor.lower() in {"unknown", "unknown vendor", "n/a", "na", "none"}:
        return ""
    return vendor


def _resolve_vendor_name(value: Any, sender: str) -> str:
    vendor = _normalize_vendor_name(value)
    sender_vendor = _extract_vendor_from_sender(sender)
    if sender_vendor and vendor and vendor.lower() in _GENERIC_VENDOR_ALIASES:
        return sender_vendor
    return vendor or sender_vendor


def _oauth_state_secret() -> str:
    try:
        from clearledgr.core.secrets import require_secret
        return require_secret("CLEARLEDGR_SECRET_KEY")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="oauth_state_signing_unavailable") from exc


def _unsign_oauth_state(state: str) -> Dict[str, Any]:
    if not state or "." not in state:
        raise HTTPException(status_code=400, detail="invalid_oauth_state")
    body, signature = state.split(".", 1)
    expected = hmac.new(
        _oauth_state_secret().encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="invalid_oauth_state_signature")
    try:
        decoded = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_oauth_state_payload") from exc
    if not isinstance(decoded, dict):
        raise HTTPException(status_code=400, detail="invalid_oauth_state_payload")
    issued_at = int(decoded.get("iat") or 0)
    max_age = int(os.getenv("GMAIL_OAUTH_STATE_MAX_AGE_SECONDS", "900") or "900")
    if issued_at and (time.time() - float(issued_at)) > max(60, max_age):
        raise HTTPException(status_code=400, detail="expired_oauth_state")
    return decoded


def _resolve_user_org_id(user_id: str) -> str:
    """Resolve org for a Gmail token user_id; fallback to default when unknown."""
    try:
        user = get_db().get_user(user_id)
    except Exception:
        user = None
    if user and user.get("organization_id"):
        return str(user["organization_id"])
    logger.warning("Unable to resolve organization for gmail user_id=%s; using default", user_id)
    return "default"


def _append_success_query(redirect_url: str, *, success: bool) -> str:
    parsed = urlsplit(str(redirect_url or "").strip())
    existing_query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing_query["success"] = "true" if success else "false"
    rebuilt_query = urlencode(existing_query)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, rebuilt_query, parsed.fragment))


def _oauth_success_page(message: str) -> HTMLResponse:
    safe_message = escape(str(message or "").strip())
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Clearledgr Gmail Connected</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #efe6d4;
        --panel: #fffdf8;
        --ink: #1d2a21;
        --muted: #5d6b61;
        --accent: #0a8f57;
        --accent-soft: rgba(10, 143, 87, 0.12);
        --border: rgba(29, 42, 33, 0.1);
        --shadow: 0 28px 90px rgba(29, 42, 33, 0.16);
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(10, 143, 87, 0.18), transparent 28%),
          radial-gradient(circle at right 18%, rgba(199, 156, 72, 0.16), transparent 22%),
          linear-gradient(180deg, #f9f5eb 0%, var(--bg) 58%, #e8dcc8 100%);
        font-family: Georgia, \"Iowan Old Style\", serif;
        color: var(--ink);
      }}
      .shell {{
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 28px;
      }}
      .card {{
        width: min(620px, calc(100vw - 32px));
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 28px;
        box-shadow: var(--shadow);
        padding: 28px 28px 26px;
        position: relative;
        overflow: hidden;
      }}
      .card::after {{
        content: "";
        position: absolute;
        inset: auto -42px -42px auto;
        width: 160px;
        height: 160px;
        border-radius: 999px;
        background: radial-gradient(circle, rgba(10, 143, 87, 0.12), transparent 66%);
        pointer-events: none;
      }}
      .topline {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 18px;
      }}
      .eyebrow {{
        display: inline-block;
        font-size: 12px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--accent);
      }}
      .pill {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--ink);
        font-size: 13px;
      }}
      .pill::before {{
        content: "";
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 0 4px rgba(10, 143, 87, 0.16);
      }}
      .hero {{
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 18px;
        align-items: start;
      }}
      .mark {{
        width: 72px;
        height: 72px;
        border-radius: 22px;
        display: grid;
        place-items: center;
        background:
          linear-gradient(180deg, rgba(10, 143, 87, 0.18), rgba(10, 143, 87, 0.08)),
          #f3fbf7;
        border: 1px solid rgba(10, 143, 87, 0.18);
        color: var(--accent);
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: clamp(36px, 5vw, 48px);
        line-height: 0.98;
        letter-spacing: -0.03em;
      }}
      .lede {{
        margin: 0;
        font-size: 18px;
        line-height: 1.6;
        color: var(--muted);
      }}
      .notes {{
        margin-top: 22px;
        display: grid;
        gap: 12px;
        position: relative;
        z-index: 1;
      }}
      .note {{
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 12px;
        padding: 14px 16px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(29, 42, 33, 0.08);
      }}
      .note strong {{
        display: block;
        margin-bottom: 3px;
        font-size: 15px;
      }}
      .note span {{
        display: block;
        color: var(--muted);
        font-size: 14px;
        line-height: 1.45;
      }}
      .note-bullet {{
        width: 12px;
        height: 12px;
        margin-top: 5px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 0 5px rgba(10, 143, 87, 0.12);
      }}
      .actions {{
        margin-top: 22px;
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        position: relative;
        z-index: 1;
      }}
      a, button {{
        appearance: none;
        border: 0;
        border-radius: 999px;
        padding: 12px 18px;
        font: inherit;
        text-decoration: none;
        cursor: pointer;
        transition: transform 140ms ease, background 140ms ease, border-color 140ms ease;
      }}
      a:hover, button:hover {{
        transform: translateY(-1px);
      }}
      .primary {{
        background: var(--accent);
        color: #fff;
        box-shadow: 0 10px 24px rgba(10, 143, 87, 0.24);
      }}
      .secondary {{
        background: rgba(255, 255, 255, 0.8);
        color: var(--ink);
        border: 1px solid rgba(29, 42, 33, 0.1);
      }}
      .meta {{
        margin-top: 16px;
        color: var(--muted);
        font-size: 13px;
      }}
      @media (max-width: 640px) {{
        .shell {{
          padding: 16px;
        }}
        .card {{
          padding: 22px 20px 20px;
          border-radius: 22px;
        }}
        .hero {{
          grid-template-columns: 1fr;
        }}
        .mark {{
          width: 64px;
          height: 64px;
          border-radius: 18px;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <section class="card">
        <div class="topline">
          <div class="eyebrow">Clearledgr AP</div>
          <div class="pill">Monitoring active</div>
        </div>
        <div class="hero">
          <div class="mark" aria-hidden="true">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" role="presentation">
              <path d="M20 7L10 17L5 12" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <div>
            <h1>Gmail connected</h1>
            <p class="lede">{safe_message}</p>
          </div>
        </div>
        <div class="notes">
          <div class="note">
            <div class="note-bullet"></div>
            <div>
              <strong>Connection is durable now</strong>
              <span>Clearledgr can keep monitoring Gmail in the background with the new refresh token.</span>
            </div>
          </div>
          <div class="note">
            <div class="note-bullet"></div>
            <div>
              <strong>Back to your inbox</strong>
              <span>You can close this tab or return to Gmail now. This page will jump back automatically in <span id="countdown">4</span> seconds.</span>
            </div>
          </div>
        </div>
        <div class="actions">
          <a class="primary" href="https://mail.google.com/mail/u/0/#inbox">Return to Gmail</a>
          <button class="secondary" type="button" onclick="window.close()">Close tab</button>
        </div>
        <div class="meta">If your browser blocks closing this tab, just switch back to Gmail.</div>
      </section>
    </main>
    <script>
      (function() {{
        var destination = "https://mail.google.com/mail/u/0/#inbox";
        var countdownNode = document.getElementById("countdown");
        var remaining = 4;
        function tick() {{
          remaining -= 1;
          if (countdownNode) countdownNode.textContent = String(Math.max(remaining, 0));
          if (remaining <= 0) {{
            window.location.href = destination;
          }}
        }}
        window.setInterval(tick, 1000);
      }})();
    </script>
  </body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/connected", include_in_schema=False)
async def gmail_connected(success: Optional[str] = None):
    is_success = str(success or "").strip().lower() == "true"
    message = (
        "Clearledgr can now continue Gmail monitoring. You can return to Gmail."
        if is_success
        else "You can close this tab and return to Gmail."
    )
    return _oauth_success_page(message)


def _assert_user_owns_gmail_identity(
    *,
    user: TokenData,
    target_user_id: str,
) -> None:
    target = str(target_user_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="missing_user_id")
    if str(user.role or "").strip().lower() in _ORG_ADMIN_ROLES:
        return
    if str(user.user_id or "").strip() != target:
        raise HTTPException(status_code=403, detail="forbidden_user_scope")


def _validate_push_payload(body: Dict[str, Any]) -> Dict[str, str]:
    message = body.get("message")
    if not isinstance(message, dict):
        raise HTTPException(status_code=400, detail="invalid_pubsub_payload")

    message_data = message.get("data")
    if not isinstance(message_data, str) or not message_data.strip():
        raise HTTPException(status_code=400, detail="missing_pubsub_message_data")

    try:
        decoded = base64.urlsafe_b64decode(message_data).decode("utf-8")
        notification = json.loads(decoded)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid_pubsub_message_data") from exc

    email_address = str(notification.get("emailAddress") or "").strip()
    history_id = str(notification.get("historyId") or "").strip()
    if not email_address or not history_id:
        raise HTTPException(status_code=400, detail="invalid_gmail_notification_payload")

    return {
        "email_address": email_address,
        "history_id": history_id,
    }


def _enforce_push_verifier(request: Request) -> None:
    """Verifier for public /gmail/push endpoint.

    If GMAIL_PUSH_SHARED_SECRET is configured, callers must present it in
    X-Gmail-Push-Token or X-Webhook-Token.
    """
    secret = str(os.getenv("GMAIL_PUSH_SHARED_SECRET", "")).strip()
    if not secret:
        if _is_prod_like_env():
            if not _allow_unverified_push_in_prod():
                raise HTTPException(status_code=503, detail="gmail_push_verifier_not_configured")
            logger.warning("GMAIL_PUSH_SHARED_SECRET is unset in prod-like env; unverified push explicitly allowed")
        return

    provided = (
        request.headers.get("X-Gmail-Push-Token")
        or request.headers.get("X-Webhook-Token")
        or ""
    ).strip()
    if provided != secret:
        raise HTTPException(status_code=401, detail="gmail_push_verification_failed")


def _should_setup_watch() -> bool:
    """
    Determine whether callback should attempt Gmail push watch setup.
    In poll mode, watch is optional and should be skipped.
    """
    mode = os.getenv("GMAIL_AUTOPILOT_MODE", "both").strip().lower() or "both"
    if mode not in {"watch", "both"}:
        return False
    topic = os.getenv("GMAIL_PUBSUB_TOPIC", "").strip()
    if not topic:
        return False
    if "your-project" in topic.lower():
        return False
    return True


class PubSubMessage(BaseModel):
    """Google Cloud Pub/Sub message format."""
    message: Dict[str, Any]
    subscription: str


class GmailAuthRequest(BaseModel):
    """Request to initiate Gmail OAuth."""
    user_id: str
    redirect_url: Optional[str] = None


class GmailCallbackRequest(BaseModel):
    """OAuth callback data."""
    code: str
    state: Optional[str] = None


# ============================================================================
# WEBHOOK ENDPOINT - Receives Pub/Sub notifications
# ============================================================================

@router.post("/push")
async def gmail_push_notification(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive Gmail push notifications from Google Cloud Pub/Sub.
    
    This endpoint is called by Google whenever a new email arrives in a watched inbox.
    Processing happens in the background to respond quickly to Google.
    """
    import time as _time
    _push_receipt_ts = _time.monotonic()
    body = await request.json()
    _enforce_push_verifier(request)
    payload = _validate_push_payload(body)
    email_address = payload["email_address"]
    history_id = payload["history_id"]

    logger.info("Received Gmail push notification for %s history=%s", email_address, history_id)
    background_tasks.add_task(
        process_gmail_notification,
        email_address,
        history_id,
        _push_receipt_ts,
    )
    return {"status": "ok"}


async def _process_label_changes(
    *,
    client: Any,
    token: Any,
    organization_id: str,
    db: Any,
    queue: Any,
    records: list,
) -> None:
    """Phase 2: bidirectional Gmail label sync.

    For every labelsAdded history record, resolve label IDs → display
    names and emit a ``LABEL_CHANGED`` agent event when:

      1. At least one added label is in ``LABEL_TO_INTENT`` (a Clearledgr
         action verb — we ignore status-only labels like "Matched" that
         we ourselves apply).
      2. The affected message's thread has an AP box in this org.

    We explicitly do NOT act on labels the agent applied itself (that
    would cause a self-feedback loop); Gmail does not tell us the
    actor, so we rely on the fact that the agent applies status labels
    while action labels (Approved, Exception, Review Required,
    Not Finance) are user verbs.

    Idempotency key: "{label_name}:{message_id}". Replaying the same
    history notification does not trigger the intent twice.
    """
    from clearledgr.core.events import AgentEvent, AgentEventType
    from clearledgr.services.gmail_labels import LABEL_TO_INTENT, intent_for_label

    if not records:
        return

    # Resolve Gmail label IDs → display names. Cache per-identity so we
    # call list_labels at most once per notification, not once per record.
    labels_by_id: Dict[str, str] = {}
    try:
        label_list = await client.list_labels()
        for lbl in (label_list or []):
            lbl_id = lbl.get("id") if isinstance(lbl, dict) else getattr(lbl, "id", None)
            lbl_name = lbl.get("name") if isinstance(lbl, dict) else getattr(lbl, "name", None)
            if lbl_id and lbl_name:
                labels_by_id[str(lbl_id)] = str(lbl_name)
    except Exception as exc:
        logger.warning("[LabelSync] list_labels failed: %s", exc)
        return

    enqueued = 0
    for rec in records:
        message_id = rec.get("message_id") or ""
        thread_id = rec.get("thread_id") or ""
        label_ids = rec.get("label_ids") or []
        if not thread_id or not label_ids:
            continue

        # Find the first action label applied in this record.
        action_label = None
        for lid in label_ids:
            name = labels_by_id.get(str(lid))
            if name and name in LABEL_TO_INTENT:
                action_label = name
                break
        if not action_label:
            continue

        # Only act if the thread has an AP box in this org.
        try:
            box = db.get_ap_item_by_thread(organization_id, thread_id)
        except Exception:
            box = None
        if not box:
            continue

        intent = intent_for_label(action_label)
        if not intent:
            continue

        event = AgentEvent(
            type=AgentEventType.LABEL_CHANGED,
            source="gmail_label_sync",
            payload={
                "box_id": box.get("id"),
                "thread_id": thread_id,
                "message_id": message_id,
                "label_name": action_label,
                "intent": intent,
                "actor_email": getattr(token, "email", None) or "gmail_user",
            },
            organization_id=organization_id,
            idempotency_key=f"label:{action_label}:{message_id}",
        )
        result = queue.enqueue(event)
        if result != "duplicate":
            enqueued += 1
            logger.info(
                "[LabelSync] Enqueued LABEL_CHANGED: box=%s label=%s intent=%s",
                box.get("id"), action_label, intent,
            )

    if enqueued > 0:
        logger.info(
            "[LabelSync] Enqueued %d LABEL_CHANGED event(s) from %d record(s)",
            enqueued, len(records),
        )


async def process_gmail_notification(email_address: str, history_id: str, push_receipt_ts: float = None):
    """
    Process a Gmail notification in the background.
    
    AP-v1 background intake flow:
    1. Fetch new emails since last history ID
    2. Classify each email (invoice/payment_request vs noise)
    3. Route AP-relevant email into canonical AP workflows
    """
    try:
        # Find the user's token by email
        token = token_store.get_by_email(email_address)
        if not token:
            logger.warning(f"No token found for {email_address}")
            return
        
        organization_id = _resolve_user_org_id(token.user_id)

        # Initialize Gmail client
        client = GmailAPIClient(token.user_id)
        if not await client.ensure_authenticated():
            logger.error(f"Failed to authenticate for {email_address}")
            return
        
        # Track autopilot state
        db = get_db()
        db.save_gmail_autopilot_state(
            user_id=token.user_id,
            email=token.email,
            last_history_id=history_id,
            last_scan_at=datetime.now(timezone.utc).isoformat(),
            last_error=None,
        )

        # Get history since last notification
        # In production, store last_history_id per user
        history = await client.get_history(history_id)
        
        # Track labelsAdded records so the bidirectional-label-sync loop
        # (Phase 2 of gmail-labels-as-AP-pipeline) can enqueue LABEL_CHANGED
        # events. Collected here so they survive the needsFullSync branch
        # (where labels can't be recovered anyway — we only act on them when
        # we have explicit history records).
        label_change_records: list = []

        if history.get("needsFullSync"):
            logger.info(f"Full sync needed for {email_address}")
            # For now, just get recent messages
            messages_response = await client.list_messages(
                query="newer_than:1d",
                max_results=50,
            )
            message_ids = [m["id"] for m in messages_response.get("messages", [])]
        else:
            # Extract new message IDs from history
            message_ids = []
            for record in history.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])
                # Phase 2 of Gmail labels pipeline: collect labelsAdded
                # records for bidirectional sync (user labels a thread →
                # agent reacts). Gmail emits one record per message with
                # label_ids; we resolve label_ids → display name below.
                for added in record.get("labelsAdded", []):
                    label_change_records.append({
                        "message_id": (added.get("message") or {}).get("id"),
                        "thread_id": (added.get("message") or {}).get("threadId"),
                        "label_ids": list(added.get("labelIds") or []),
                    })

        if not message_ids and not label_change_records:
            logger.info(f"No new messages or label changes for {email_address}")
            return
        
        logger.info(f"Processing {len(message_ids)} new messages for {email_address}")

        # §2: Enqueue events to durable queue instead of inline processing
        from clearledgr.core.events import AgentEvent, AgentEventType
        from clearledgr.core.event_queue import get_event_queue

        queue = get_event_queue()
        enqueued = 0
        db = get_db()
        for message_id in message_ids:
            try:
                # §2.2: Detect if this is a reply on a watched thread (vendor response)
                # vs a new email. Check if thread belongs to existing Box.
                event_type = AgentEventType.EMAIL_RECEIVED
                thread_id_for_event = ""
                try:
                    # Fetch thread_id from the message
                    msg_meta = await client.get_message(message_id, format="metadata")
                    thread_id_for_event = getattr(msg_meta, "thread_id", "") or ""
                    if thread_id_for_event:
                        existing_item = db.get_ap_item_by_thread(organization_id, thread_id_for_event)
                        if existing_item:
                            # This is a reply on a watched thread — vendor response
                            event_type = AgentEventType.VENDOR_RESPONSE_RECEIVED
                except Exception:
                    pass  # Fall back to EMAIL_RECEIVED

                event = AgentEvent(
                    type=event_type,
                    source="gmail_pubsub",
                    payload={
                        "message_id": message_id,
                        "thread_id": thread_id_for_event,
                        "mailbox": email_address,
                        "user_id": token.user_id,
                        "vendor_id": (existing_item.get("vendor_name", "") if event_type == AgentEventType.VENDOR_RESPONSE_RECEIVED else ""),
                    },
                    organization_id=organization_id,
                    idempotency_key=message_id,
                )
                result = queue.enqueue(event)
                if result != "duplicate":
                    enqueued += 1
                    # §11: Record email_receipt_to_queue SLA latency
                    if push_receipt_ts is not None:
                        try:
                            import time as _t
                            from clearledgr.core.sla_tracker import get_sla_tracker
                            latency_ms = int((_t.monotonic() - push_receipt_ts) * 1000)
                            get_sla_tracker().record(
                                "email_receipt_to_queue", latency_ms,
                                ap_item_id=message_id,
                                organization_id=organization_id,
                            )
                        except Exception:
                            pass
            except Exception as e:
                # Fallback: process inline if queue unavailable
                logger.warning("Event queue unavailable, processing inline: %s", e)
                try:
                    await process_single_email(
                        client=client,
                        message_id=message_id,
                        user_id=token.user_id,
                        organization_id=organization_id,
                    )
                except Exception as inner_e:
                    logger.error(f"Error processing message {message_id}: {inner_e}")

        # Events are now in the Redis Stream. Celery workers consume from
        # the stream via the Beat-scheduled reclaim task + direct stream
        # consumers. No direct Celery dispatch needed — that would cause
        # double-processing.
        if enqueued > 0:
            logger.info(
                "Enqueued %d/%d email events to durable queue for %s",
                enqueued, len(message_ids), email_address,
            )

        # ── Phase 2: Bidirectional Gmail label sync ──
        #
        # For every labelsAdded record, resolve the label IDs → display
        # names and enqueue a LABEL_CHANGED event if it matches a
        # Clearledgr action label and the thread has an AP box.
        if label_change_records:
            try:
                await _process_label_changes(
                    client=client,
                    token=token,
                    organization_id=organization_id,
                    db=db,
                    queue=queue,
                    records=label_change_records,
                )
            except Exception as exc:
                logger.warning("[LabelSync] Failed to process label changes: %s", exc)

        logger.info(f"Finished processing emails for {email_address}")
    
    except Exception as e:
        logger.error(f"Error in process_gmail_notification: {e}")
        try:
            db = get_db()
            db.save_gmail_autopilot_state(
                user_id=token.user_id if token else "unknown",
                email=email_address,
                last_error=str(e),
            )
        except Exception:
            pass


async def process_single_email(
    client: GmailAPIClient,
    message_id: str,
    user_id: str,
    organization_id: str,
):
    """
    Process a single email autonomously.
    """
    # Fetch the full message
    message = await client.get_message(message_id)
    
    db = get_db()

    existing_finance_email = db.get_finance_email_by_gmail_id(message.id)
    existing_ap_item = None
    if hasattr(db, "get_ap_item_by_thread"):
        try:
            existing_ap_item = db.get_ap_item_by_thread(organization_id, message.thread_id)
        except Exception:
            existing_ap_item = None
    if not existing_ap_item and hasattr(db, "get_ap_item_by_message_id"):
        try:
            existing_ap_item = db.get_ap_item_by_message_id(organization_id, message.id)
        except Exception:
            existing_ap_item = None

    # Skip only when the finance email is already linked to a canonical AP item.
    # Historical planner failures left detected finance_emails without ap_items;
    # those messages must be allowed to re-enter processing.
    if existing_finance_email and existing_ap_item:
        return
    if "CLEARLEDGR_PROCESSED" in message.labels and existing_ap_item:
        return
    
    # Classify the email for AP workflow
    classification = await classify_email_with_llm(
        subject=message.subject,
        sender=message.sender,
        snippet=message.snippet,
        body=message.body_text[:2000],  # Limit for LLM
        attachments=message.attachments or [],
    )
    
    logger.info(
        "Email '%s' classified as: %s (%.2f)",
        message.subject,
        classification.get("type"),
        classification.get("confidence", 0.0),
    )

    category = str(classification.get("type") or "").lower()
    label_parse = _label_only_document_parse(message)
    label_only_category = _label_only_document_type(message, label_parse)

    # Non-AP finance docs still need to be labeled in Gmail even when they do
    # not enter the AP workflow.
    if classification.get("type") == "NOISE" or classification.get("confidence", 0) < 0.7:
        if label_only_category in {"payment", "receipt", "refund", "credit_note", "statement"}:
            finance_email = _save_label_only_finance_email(
                db,
                message=message,
                user_id=user_id,
                organization_id=organization_id,
                document_type=label_only_category,
                parsed=label_parse,
            )
            await _sync_message_finance_labels(
                client,
                user_id=user_id,
                organization_id=organization_id,
                message_id=message.id,
                thread_id=getattr(message, "thread_id", None),
                finance_email=finance_email,
                document_type=label_only_category,
                db=db,
            )
        else:
            logger.info("Skipping non-AP email: %s", message.subject)
        return

    # Use document routing table to decide workflow
    from clearledgr.services.document_routing import get_route, AP_ITEM_TYPES

    route = get_route(category)

    # Non-AP document types: record for tracking, label in Gmail, skip AP workflow
    if category not in AP_ITEM_TYPES:
        resolved_type = label_only_category if label_only_category in {
            "payment", "receipt", "refund", "credit_note", "statement",
            "subscription", "remittance_advice", "bank_notification",
        } else category
        if resolved_type != "noise":
            finance_email = _save_label_only_finance_email(
                db,
                message=message,
                user_id=user_id,
                organization_id=organization_id,
                document_type=resolved_type,
                parsed=label_parse,
            )
            await _sync_message_finance_labels(
                client,
                user_id=user_id,
                organization_id=organization_id,
                message_id=message.id,
                thread_id=getattr(message, "thread_id", None),
                finance_email=finance_email,
                document_type=resolved_type,
                db=db,
            )
            logger.info(
                "%s recorded (no AP workflow): %s from %s",
                route.label, message.subject, message.sender,
            )
        else:
            logger.info("Skipping non-finance email: %s", message.subject)
        return

    # Store as detected finance email
    received_at = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
    finance_email_payload = {
        "gmail_id": message.id,
        "subject": message.subject or "",
        "sender": message.sender or "",
        "received_at": received_at,
        "email_type": category,
        "confidence": classification.get("confidence", 0.0),
        "status": "detected",
        "organization_id": organization_id,
        "user_id": user_id,
        "metadata": _merge_finance_email_metadata(
            existing_finance_email,
            {
                "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
                "classifier": classification,
                "attachment_manifest": _attachment_manifest(message.attachments or []),
            },
        ),
    }
    existing_finance_email_id = str(getattr(existing_finance_email, "id", "") or "").strip()
    if existing_finance_email_id:
        finance_email_payload["id"] = existing_finance_email_id
    db.save_finance_email(FinanceEmail(**finance_email_payload))
    
    # All AP_ITEM_TYPES go through the invoice processing pipeline.
    # The routing table determines the initial state (received vs closed)
    # and whether approval is needed. No hardcoded type checks.
    await process_invoice_email(
        client=client,
        message=message,
        user_id=user_id,
        organization_id=organization_id,
        confidence=classification.get("confidence", 0.0),
        document_type=category,
    )


async def classify_email_with_llm(
    subject: str,
    sender: str,
    snippet: str,
    body: str,
    attachments: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Classify an email for AP workflow.
    
    Returns:
        Dict with 'type' (INVOICE | PAYMENT_REQUEST | NOISE) and 'confidence'
    """
    from clearledgr.services.ap_classifier import classify_ap_email

    return classify_ap_email(
        subject=subject or "",
        sender=sender or "",
        snippet=snippet or "",
        body=body or "",
        attachments=attachments or [],
    )


def classify_email_heuristic(subject: str, sender: str, snippet: str) -> Dict[str, Any]:
    """Deprecated: retained for backward compatibility."""
    from clearledgr.services.ap_classifier import classify_ap_email

    return classify_ap_email(subject=subject, sender=sender, snippet=snippet, body="")


async def process_invoice_email(
    client: GmailAPIClient,
    message,
    user_id: str,
    organization_id: str,
    confidence: float,
    *,
    document_type: str = "invoice",
    run_runtime: bool = True,
    create_draft: bool = True,
    refresh_reason: Optional[str] = None,
):
    """
    Process an invoice email through the invoice workflow.

    This is the main entry point for invoice processing from Gmail Pub/Sub.

    Flow:
    1. Extract invoice data using Claude Vision (for PDFs) or LLM (for text)
    2. Submit to invoice workflow
    3. Workflow handles: auto-approve (high confidence) or route to Slack (low confidence)
    """
    # D2: Check subscription limits before processing
    try:
        from clearledgr.services.subscription import get_subscription_service
        sub_svc = get_subscription_service()
        sub = sub_svc.get_subscription(organization_id)
        current_usage = sub.usage.invoices_this_month if sub.usage else 0
        limit_check = sub_svc.check_limit(organization_id, "invoices_per_month", current_usage)
        if not limit_check.get("allowed", True):
            logger.warning("Subscription limit reached for org %s, skipping invoice processing", organization_id)
            return
    except Exception as sub_exc:
        logger.warning("Subscription check failed for org %s, proceeding: %s", organization_id, sub_exc)

    from clearledgr.services.invoice_workflow import InvoiceWorkflowService, InvoiceData
    from clearledgr.workflows.gmail_activities import extract_email_data_activity

    logger.info(f"Processing invoice email: {message.subject}")
    
    # Extract data from email + attachments
    attachments_with_content = []
    
    # Fetch attachment content for PDFs/images (for Claude Vision)
    for attachment in message.attachments or []:
        try:
            content_type = (
                attachment.get("mime_type")
                or attachment.get("mimeType")
                or attachment.get("content_type")
                or ""
            ).lower()
            filename = (attachment.get("filename") or attachment.get("name") or "").lower()
            
            # Only fetch PDFs and images for vision extraction
            if (
                "pdf" in content_type
                or filename.endswith(".pdf")
                or "image" in content_type
                or any(filename.endswith(ext) for ext in [".png", ".jpg", ".jpeg"])
                or filename.endswith(".docx")
                or "wordprocessingml" in content_type
            ):
                
                # Fetch the attachment content
                attachment_bytes = await client.get_attachment(
                    message_id=message.id,
                    attachment_id=attachment.get("attachmentId") or attachment.get("id"),
                )
                
                if attachment_bytes:
                    # Convert bytes to base64 for Claude Vision
                    import base64
                    content_base64 = base64.b64encode(attachment_bytes).decode("utf-8")
                    
                    attachments_with_content.append({
                        "filename": attachment.get("filename") or attachment.get("name"),
                        "content_type": content_type,
                        "content_base64": content_base64,
                    })
                    logger.info(f"Fetched attachment for vision: {attachment.get('filename')}")
        except Exception as e:
            logger.warning(f"Failed to fetch attachment {attachment.get('filename')}: {e}")
    
    # Extract invoice data using deterministic parser + LLM fallback
    extraction: Dict[str, Any] = {}
    try:
        extraction = await extract_email_data_activity({
            "subject": message.subject,
            "sender": message.sender,
            "snippet": message.snippet,
            "body": message.body_text or "",
            "attachments": attachments_with_content,
        })
    except Exception as e:
        logger.warning(f"Extraction failed, continuing with sender fallback: {e}")
        extraction = {}

    vendor_name = _resolve_vendor_name(extraction.get("vendor"), message.sender)
    extracted_currency = str(extraction.get("currency") or "USD").strip().upper() or "USD"
    extracted_document_type = (
        str(extraction.get("document_type") or extraction.get("email_type") or "invoice")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    ) or "invoice"
    subject_only = str(getattr(message, "subject", "") or "").lower()
    if re.search(r"\b(credit note|credit memo)\b", subject_only):
        extracted_document_type = "credit_note"
    elif re.search(r"\brefund\b", subject_only):
        extracted_document_type = "refund"
    elif re.search(r"\b(payment confirmation|payment received|payment processed|payment successful|payment completed)\b", subject_only):
        extracted_document_type = "payment"
    if extracted_document_type not in {"invoice", "payment", "receipt", "refund", "credit_note", "payment_request", "statement", "other"}:
        extracted_document_type = "invoice"
    try:
        extracted_amount = float(
            extraction.get("amount")
            or extraction.get("total_amount")
            or 0.0
        )
    except (TypeError, ValueError):
        extracted_amount = 0.0
    extracted_confidence = extraction.get("confidence", confidence)

    def _safe_date(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).date().isoformat()
        except Exception:
            return None

    extraction_gate = _build_extraction_review_gate(
        extraction=extraction,
        amount=extracted_amount,
        currency=extracted_currency,
        invoice_number=extraction.get("invoice_number"),
        due_date=_safe_date(extraction.get("due_date")),
        confidence=float(extracted_confidence or 0.0),
    )
    requires_field_review = bool(extraction_gate.get("requires_field_review"))
    confidence_blockers = extraction_gate.get("confidence_blockers") if isinstance(extraction_gate.get("confidence_blockers"), list) else []
    source_conflicts = extraction.get("source_conflicts") if isinstance(extraction.get("source_conflicts"), list) else []
    conflict_actions = extraction.get("conflict_actions") if isinstance(extraction.get("conflict_actions"), list) else []
    blocking_conflicts = _conflict_blockers(source_conflicts)
    extraction_exception_code = "field_conflict" if blocking_conflicts else None
    extraction_exception_severity = "high" if blocking_conflicts else None

    db = get_db()
    existing_finance_email = db.get_finance_email_by_gmail_id(message.id) if hasattr(db, "get_finance_email_by_gmail_id") else None
    received_at = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
    attachment_manifest = _attachment_manifest(message.attachments or [])
    extraction_metadata = _merge_finance_email_metadata(
        existing_finance_email,
        {
            "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
            "attachment_manifest": attachment_manifest,
            "fetched_attachment_manifest": _attachment_manifest(attachments_with_content, include_content=True),
            "attachment_count": len(attachment_manifest),
            "extraction_method": extraction.get("extraction_method"),
            "extraction_model": extraction.get("extraction_model"),
            "primary_source": extraction.get("primary_source"),
            "document_type": extracted_document_type,
            "email_type": extracted_document_type,
            "field_confidences": extraction.get("field_confidences") or {},
            "field_provenance": extraction.get("field_provenance") or {},
            "field_evidence": extraction.get("field_evidence") or {},
            "source_conflicts": source_conflicts,
            "confidence_gate": extraction_gate,
            "requires_field_review": requires_field_review,
            "confidence_blockers": confidence_blockers,
            "requires_extraction_review": bool(extraction.get("requires_extraction_review")),
            "conflict_actions": conflict_actions,
            "raw_parser": extraction.get("raw_parser") or {},
            "reasoning_summary": extraction.get("reasoning_summary"),
            "payment_processor": extraction.get("payment_processor"),
            "invoice_date": extraction.get("invoice_date"),
            "exception_code": extraction_exception_code,
            "exception_severity": extraction_exception_severity,
            "zero_amount_confirmed_by_attachment": bool(
                extracted_amount == 0.0 and (extraction.get("raw_parser") or {}).get("has_invoice_attachment")
            ),
        },
    )
    finance_email_payload = {
        "gmail_id": message.id,
        "subject": message.subject or "",
        "sender": message.sender or "",
        "received_at": received_at,
        "email_type": extracted_document_type,
        "confidence": extracted_confidence,
        "vendor": vendor_name or None,
        "amount": extracted_amount,
        "currency": extracted_currency,
        "invoice_number": extraction.get("invoice_number"),
        "status": "processing",
        "organization_id": organization_id,
        "user_id": user_id,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "metadata": extraction_metadata,
    }
    existing_finance_email_id = str(getattr(existing_finance_email, "id", "") or "").strip()
    if existing_finance_email_id:
        finance_email_payload["id"] = existing_finance_email_id
    db.save_finance_email(FinanceEmail(**finance_email_payload))
    
    # Combine all text for discount detection
    invoice_text = f"{message.subject}\n{message.snippet}\n{message.body_text or ''}"
    
    # Build invoice data object
    invoice = InvoiceData(
        gmail_id=message.id,
        subject=message.subject,
        sender=message.sender,
        vendor_name=vendor_name,
        amount=extracted_amount,
        currency=extracted_currency,
        invoice_number=extraction.get("invoice_number"),
        due_date=_safe_date(extraction.get("due_date")),
        confidence=extracted_confidence,
        user_id=user_id,
        organization_id=organization_id,
        invoice_text=invoice_text,  # For discount detection
        line_items=extraction.get("line_items") if isinstance(extraction.get("line_items"), list) else None,
    )
    
    if extracted_document_type != "invoice":
        finance_email_payload["status"] = "processed"
        saved_finance_email = db.save_finance_email(FinanceEmail(**finance_email_payload))
        try:
            await _sync_message_finance_labels(
                client,
                user_id=user_id,
                organization_id=organization_id,
                message_id=message.id,
                thread_id=getattr(message, "thread_id", None),
                finance_email=saved_finance_email,
                ap_item={"metadata": {"document_type": extracted_document_type, "email_type": extracted_document_type}},
                document_type=extracted_document_type,
                db=db,
            )
        except Exception as exc:
            logger.warning("Could not sync Gmail labels for non-invoice finance doc %s: %s", message.id, exc)
        return {
            "status": "processed_non_invoice",
            "execution_mode": "classification_correction",
            "document_type": extracted_document_type,
            "email_id": message.id,
        }

    # Submit through canonical finance runtime (AP skill v1)
    runtime_invoice_payload = {
        **invoice.__dict__,
        "thread_id": getattr(message, "thread_id", None) or message.id,
        "message_id": message.id,
        "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
        "gmail_message_id": message.id,
        "snippet": message.snippet or "",
        "body": (message.body_text or "")[:4000],
        "document_type": extracted_document_type,
        "email_type": extracted_document_type,
        "intake_source": "gmail_autopilot" if run_runtime else "gmail_replay_refresh",
        "field_confidences": extraction.get("field_confidences") or {},
        "raw_parser": extraction.get("raw_parser") or {},
        "extraction_method": extraction.get("extraction_method"),
        "extraction_model": extraction.get("extraction_model"),
        "reasoning_summary": extraction.get("reasoning_summary"),
        "payment_processor": extraction.get("payment_processor"),
        "invoice_date": extraction.get("invoice_date"),
        "primary_source": extraction.get("primary_source"),
        "attachment_manifest": attachment_manifest,
        "zero_amount_confirmed_by_attachment": extraction_metadata.get("zero_amount_confirmed_by_attachment"),
        "field_provenance": extraction.get("field_provenance") or {},
        "field_evidence": extraction.get("field_evidence") or {},
        "source_conflicts": source_conflicts,
        "confidence_gate": extraction_gate,
        "requires_field_review": requires_field_review,
        "confidence_blockers": confidence_blockers,
        "requires_extraction_review": bool(extraction.get("requires_extraction_review")),
        "conflict_actions": conflict_actions,
        "exception_code": extraction_exception_code,
        "exception_severity": extraction_exception_severity,
        "document_type": document_type,
    }

    try:
        from clearledgr.services.finance_agent_runtime import get_platform_finance_runtime

        runtime = get_platform_finance_runtime(invoice.organization_id or organization_id)
        if run_runtime:
            result = await runtime.execute_ap_invoice_processing(
                invoice_payload=runtime_invoice_payload,
                attachments=attachments_with_content,
                correlation_id=invoice.correlation_id,
            )
        else:
            result = runtime.refresh_invoice_record_from_extraction(
                invoice_payload=runtime_invoice_payload,
                attachments=attachments_with_content,
                correlation_id=invoice.correlation_id,
                refresh_reason=refresh_reason,
            )
        logger.info("Finance runtime AP result: %s", result.get("status"))
    except Exception as e:
        logger.error("Finance runtime AP processing failed: %s", e)
        result = {"status": "error", "error": str(e)}

    if result.get("reason") == "field_review_required" or requires_field_review:
        finance_email_payload["status"] = "review_required"
    else:
        finance_email_payload["status"] = "processed" if result.get("status") not in {"error", "failed"} else "detected"
    saved_finance_email = db.save_finance_email(FinanceEmail(**finance_email_payload))

    try:
        await _sync_message_finance_labels(
            client,
            user_id=user_id,
            organization_id=organization_id,
            message_id=message.id,
            thread_id=getattr(message, "thread_id", None),
            finance_email=saved_finance_email,
            document_type=None if extracted_document_type == "invoice" else extracted_document_type,
            db=db,
        )
    except Exception as exc:
        logger.warning("Could not sync Gmail labels for invoice %s: %s", message.id, exc)

    # Create draft summary on the thread (Fyxer pattern)
    # User opens the thread → sees a draft with the extracted invoice data
    if create_draft:
        try:
            await _create_invoice_draft_summary(client, message, invoice)
        except Exception as exc:
            logger.warning("Could not create draft summary: %s", exc)

    return result


async def _create_invoice_draft_summary(client: GmailAPIClient, message, invoice):
    """Create a Gmail draft reply summarizing the extracted invoice data.

    Fyxer pattern: the user opens a thread and finds a draft with structured
    invoice data ready to forward, approve, or reference.
    """
    vendor = invoice.vendor_name or "Unknown"
    amount = invoice.amount or 0
    currency = invoice.currency or "USD"
    inv_num = invoice.invoice_number or "N/A"
    due = invoice.due_date or "Not specified"

    amount_str = f"${amount:,.2f}" if isinstance(amount, (int, float)) and amount else str(amount)

    body = (
        f"Clearledgr detected an invoice in this thread.\n\n"
        f"  Vendor:     {vendor}\n"
        f"  Amount:     {amount_str} {currency}\n"
        f"  Invoice #:  {inv_num}\n"
        f"  Due date:   {due}\n"
        f"  Confidence: {invoice.confidence:.0%}\n\n"
        f"Status: Needs approval\n"
        f"---\n"
        f"This draft was created by Clearledgr. "
        f"Delete it if not needed, or forward it to your approver."
    )

    thread_id = getattr(message, 'thread_id', None) or message.id
    to_addr = getattr(message, 'sender', '') or ''

    await client.create_draft(
        thread_id=thread_id,
        to=to_addr,
        subject=f"Re: {message.subject or 'Invoice'}",
        body=body,
    )


def _extract_vendor_from_sender(sender: str) -> str:
    """Extract vendor name from email sender."""
    import re
    # Try to get name part: "Stripe <billing@stripe.com>"
    name_match = re.match(r"^([^<]+)", sender)
    if name_match:
        return name_match.group(1).strip()
    # Fall back to domain: "billing@stripe.com" -> "stripe"
    if "@" in sender:
        domain = sender.split("@")[1].split(".")[0]
        return domain.title()
    return sender


async def process_payment_request_email(
    client: GmailAPIClient,
    message,
    user_id: str,
    organization_id: str,
    confidence: float,
):
    """
    Process a payment request email (non-invoice).
    
    These are emails like:
    - "Please pay $500 to John for consulting"
    - "Expense reimbursement request: $250"
    - "Contractor payment needed"
    
    Flow:
    1. Extract payment details from email
    2. Create payment request
    3. Route to appropriate approver via Slack
    """
    from clearledgr.services.payment_request import get_payment_request_service
    from clearledgr.services.slack_notifications import send_payment_request_notification
    
    logger.info(f"Processing payment request email: {message.subject}")
    db = get_db()
    existing_finance_email = db.get_finance_email_by_gmail_id(message.id) if hasattr(db, "get_finance_email_by_gmail_id") else None
    
    # Get sender info
    sender_name = _extract_vendor_from_sender(message.sender)
    sender_email = message.sender
    if "<" in sender_email:
        import re
        email_match = re.search(r'<([^>]+)>', sender_email)
        if email_match:
            sender_email = email_match.group(1)
    
    # Create payment request
    service = get_payment_request_service(organization_id)
    
    try:
        request = service.create_from_email(
            email_id=message.id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=message.subject,
            body=message.body_text or message.snippet or "",
        )
        
        logger.info(f"Created payment request {request.request_id}: ${request.amount} to {request.payee_name}")
        
        # Send Slack notification for approval
        try:
            await send_payment_request_notification(request)
        except Exception as e:
            logger.warning(f"Failed to send Slack notification: {e}")

        saved_finance_email = None
        if hasattr(db, "save_finance_email"):
            received_at = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
            payload = {
                "gmail_id": message.id,
                "subject": message.subject or "",
                "sender": message.sender or "",
                "received_at": received_at,
                "email_type": "payment_request",
                "confidence": confidence,
                "vendor": sender_name or None,
                "amount": getattr(request, "amount", None),
                "currency": str(getattr(request, "currency", "USD") or "USD").strip().upper() or "USD",
                "invoice_number": None,
                "status": "processed",
                "organization_id": organization_id,
                "user_id": user_id,
                "metadata": _merge_finance_email_metadata(
                    existing_finance_email,
                    {
                        "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
                        "attachment_manifest": _attachment_manifest(getattr(message, "attachments", None) or []),
                        "payment_request_id": getattr(request, "request_id", None),
                        "payment_request_status": "created",
                        "email_type": "payment_request",
                    },
                ),
            }
            existing_id = str(getattr(existing_finance_email, "id", "") or "").strip()
            if existing_id:
                payload["id"] = existing_id
            saved_finance_email = db.save_finance_email(FinanceEmail(**payload))

        try:
            await _sync_message_finance_labels(
                client,
                user_id=user_id,
                organization_id=organization_id,
                message_id=message.id,
                thread_id=getattr(message, "thread_id", None),
                finance_email=saved_finance_email or existing_finance_email,
                document_type="payment_request",
                db=db,
            )
        except Exception as exc:
            logger.warning("Could not sync Gmail labels for payment request %s: %s", message.id, exc)
        
        return {
            "status": "created",
            "request_id": request.request_id,
            "amount": request.amount,
            "payee": request.payee_name,
        }
    
    except Exception as e:
        logger.error(f"Payment request creation failed: {e}")
        if hasattr(db, "save_finance_email"):
            received_at = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
            payload = {
                "gmail_id": message.id,
                "subject": message.subject or "",
                "sender": message.sender or "",
                "received_at": received_at,
                "email_type": "payment_request",
                "confidence": confidence,
                "vendor": sender_name or None,
                "status": "detected",
                "organization_id": organization_id,
                "user_id": user_id,
                "metadata": _merge_finance_email_metadata(
                    existing_finance_email,
                    {
                        "gmail_thread_id": getattr(message, "thread_id", None) or message.id,
                        "attachment_manifest": _attachment_manifest(getattr(message, "attachments", None) or []),
                        "payment_request_status": "error",
                        "payment_request_error": str(e),
                        "email_type": "payment_request",
                    },
                ),
            }
            existing_id = str(getattr(existing_finance_email, "id", "") or "").strip()
            if existing_id:
                payload["id"] = existing_id
            saved_finance_email = db.save_finance_email(FinanceEmail(**payload))
            try:
                await _sync_message_finance_labels(
                    client,
                    user_id=user_id,
                    organization_id=organization_id,
                    message_id=message.id,
                    thread_id=getattr(message, "thread_id", None),
                    finance_email=saved_finance_email,
                    document_type="payment_request",
                    db=db,
                )
            except Exception as exc:
                logger.warning("Could not sync Gmail labels for failed payment request %s: %s", message.id, exc)
        return {"status": "error", "error": str(e)}


@router.get("/callback")
async def gmail_callback(code: str, state: Optional[str] = None):
    """
    Handle OAuth callback from Google.
    """
    try:
        # Decode and verify OAuth state.
        if not state:
            raise HTTPException(status_code=400, detail="missing_oauth_state")
        state_decoded = _unsign_oauth_state(state)
        user_id = state_decoded.get("user_id")
        redirect_url = state_decoded.get("redirect_url")
        oauth_redirect_uri = state_decoded.get("oauth_redirect_uri")
        
        # Exchange code for tokens
        token = await exchange_code_for_tokens(code, redirect_uri=oauth_redirect_uri)

        # Override user_id if provided in state
        if user_id:
            token = token.__class__(
                user_id=user_id,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                expires_at=token.expires_at,
                email=token.email,
            )

        existing_token = token_store.get(token.user_id) if token.user_id else None
        if (
            not str(token.refresh_token or "").strip()
            and existing_token
            and str(existing_token.refresh_token or "").strip()
        ):
            token = token.__class__(
                user_id=token.user_id,
                access_token=token.access_token,
                refresh_token=existing_token.refresh_token,
                expires_at=token.expires_at,
                email=token.email,
            )

        # Store token
        token_store.store(token)

        # Pre-create Clearledgr labels so they appear in Gmail immediately
        try:
            from clearledgr.services.gmail_labels import CLEARLEDGR_LABELS, ensure_label
            label_client = GmailAPIClient(token.user_id)
            if await label_client.ensure_authenticated():
                for key in CLEARLEDGR_LABELS:
                    await ensure_label(label_client, key, token.email or "")
                logger.info("Pre-created Clearledgr labels for %s", token.email)
        except Exception as exc:
            logger.warning("Could not pre-create labels: %s", exc)

        watch_result: Dict[str, Any] = {}
        watch_status = "skipped"
        watch_error: Optional[str] = None

        if _should_setup_watch():
            try:
                watch_service = GmailWatchService(token.user_id)
                watch_result = await watch_service.setup_watch()
                watch_status = "enabled"
                logger.info(
                    "Gmail watch set up for %s, expires: %s",
                    token.email,
                    watch_result.get("expiration"),
                )
            except Exception as exc:
                # Keep Gmail OAuth connected even if Pub/Sub watch setup fails;
                # poll mode can continue to process messages.
                watch_status = "failed"
                watch_error = str(exc)
                logger.warning("Gmail watch setup failed for %s: %s", token.email, exc)
        else:
            logger.info("Skipping Gmail watch setup (poll mode or topic not configured)")

        # Mark autopilot connected immediately after OAuth (watch is optional).
        db = get_db()
        db.save_gmail_autopilot_state(
            user_id=token.user_id,
            email=token.email,
            last_history_id=watch_result.get("historyId") if watch_result else None,
            watch_expiration=watch_result.get("expiration") if watch_result else None,
            last_watch_at=datetime.now(timezone.utc).isoformat() if watch_result else None,
            last_error=watch_error,
        )
        
        # Return success or redirect
        if redirect_url:
            from fastapi.responses import RedirectResponse
            safe_redirect = _append_success_query(redirect_url, success=True)
            if str(redirect_url).startswith("/workspace"):
                return _oauth_success_page("Clearledgr connected Gmail successfully. Return to Gmail to continue.")
            return RedirectResponse(url=safe_redirect)
        
        return {
            "status": "success",
            "email": token.email,
            "message": "Gmail autopilot enabled. Clearledgr will now process your emails automatically.",
            "watch_status": watch_status,
            "watch_error": watch_error,
            "watch_expiration": watch_result.get("expiration"),
        }
    
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=503, detail=safe_error(e, "gmail callback config"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=safe_error(e, "gmail callback"))


@router.post("/disconnect")
async def gmail_disconnect(
    user_id: str,
    user: TokenData = Depends(get_current_user),
):
    """
    Disconnect Gmail integration for a user.
    """
    _assert_user_owns_gmail_identity(user=user, target_user_id=user_id)
    try:
        # Stop watch
        watch_service = GmailWatchService(user_id)
        await watch_service.stop_watch()
        
        # Remove token
        token_store.delete(user_id)
        
        return {"status": "success", "message": "Gmail disconnected"}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=safe_error(e, "gmail disconnect"))


@router.get("/status/{user_id}")
async def gmail_status(
    user_id: str,
    user: TokenData = Depends(get_current_user),
):
    """
    Check Gmail integration status for a user.
    """
    _assert_user_owns_gmail_identity(user=user, target_user_id=user_id)
    token = token_store.get(user_id)
    state = get_db().get_gmail_autopilot_state(user_id) or {}
    
    if not token:
        return {
            "connected": False,
            "message": "Gmail not connected",
            "autopilot": {
                "last_scan_at": state.get("last_scan_at"),
                "last_error": state.get("last_error"),
            },
        }
    
    return {
        "connected": True,
        "email": token.email,
        "expires_at": token.expires_at.isoformat(),
        "is_expired": token.is_expired(),
        "autopilot": {
            "last_scan_at": state.get("last_scan_at"),
            "watch_expiration": state.get("watch_expiration"),
            "last_watch_at": state.get("last_watch_at"),
            "last_error": state.get("last_error"),
        },
    }
